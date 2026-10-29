from __future__ import annotations

import ast
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import Settings
from .context import test_node_ids
from .runner import git_diff, run_pytest, sha256_file


class MiniMaxError(RuntimeError):
    pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MiniMaxClient:
    def __init__(self, settings: Settings, timeout: int = 120):
        if not settings.minimax_api_key:
            raise MiniMaxError(
                "MINIMAX_API_KEY is not set; put it in the environment or .env before running assist"
            )
        self.settings = settings
        self.timeout = timeout

    def _url(self) -> str:
        base = self.settings.minimax_base_url
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        if self.settings.minimax_group_id:
            url += "?" + urlencode({"GroupId": self.settings.minimax_group_id})
        return url

    def chat(self, messages: list[dict], max_tokens: int = 3000, temperature: float = 0.1) -> dict:
        payload = {
            "model": self.settings.minimax_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        request = Request(
            self._url(),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.minimax_api_key}",
            },
            method="POST",
        )
        started = time.time()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw_text = response.read().decode("utf-8")
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise MiniMaxError(
                f"MiniMax HTTP {error.code}: {detail[:1200]}"
            ) from error
        except URLError as error:
            raise MiniMaxError(f"MiniMax is not reachable: {error.reason}") from error
        latency = round(time.time() - started, 3)
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError as error:
            raise MiniMaxError(f"MiniMax returned non-JSON: {raw_text[:500]}") from error
        base_resp = raw.get("base_resp") or {}
        if base_resp.get("status_code") not in (None, 0):
            raise MiniMaxError(
                f"MiniMax error {base_resp.get('status_code')}: {base_resp.get('status_msg')}"
            )
        choices = raw.get("choices") or []
        if not choices:
            raise MiniMaxError(f"MiniMax returned no choices: {raw_text[:800]}")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        if not isinstance(content, str) or not content.strip():
            raise MiniMaxError("MiniMax returned empty content")
        return {
            "content": content,
            "usage": raw.get("usage", {}),
            "raw": raw,
            "latency_seconds": latency,
            "model": self.settings.minimax_model,
            "base_url": self.settings.minimax_base_url,
        }

    def check(self) -> dict:
        result = self.chat(
            [{"role": "user", "content": "Reply with the single word OK."}],
            max_tokens=16,
            temperature=0.0,
        )
        return {
            "model": result["model"],
            "base_url": result["base_url"],
            "reply": result["content"].strip()[:200],
            "usage": result["usage"],
            "latency_seconds": result["latency_seconds"],
        }


def build_messages(
    settings: Settings,
    context: dict,
    task_text: str,
    baseline_log: str,
) -> list[dict]:
    target_files = []
    seen = set()
    for item in context["sources"]["code"]:
        if item["path"] in seen:
            continue
        seen.add(item["path"])
        path = settings.project_root / item["path"]
        target_files.append((item["path"], path.read_text(encoding="utf-8")))

    spec_text = "\n\n".join(
        f"## {spec['req_id']} {spec['title']} ({spec['path']}:{spec['line_start']}-{spec['line_end']})\n"
        f"{spec['body']}"
        for spec in context["sources"]["specs"]
    )
    target_tests = "\n".join(
        f"- {test['path']}::{test['name']}\n```python\n{test['source']}\n```"
        for test in context["sources"]["tests"]
        if test["kind"] == "target"
    )
    regression_tests = "\n".join(
        f"- {test['path']}::{test['name']}"
        for test in context["sources"]["tests"]
        if test["kind"] == "regression"
    )
    files_text = "\n\n".join(
        f"## File `{path}`\n```python\n{content}\n```" for path, content in target_files
    )
    state = context.get("state", {})
    state_text = (
        f"completed={', '.join(state.get('completed', [])) or '(none)'}; "
        f"pending={', '.join(state.get('pending', [])) or '(none)'}"
    )
    system = (
        "You are a precise software engineer working on a spec-driven development task. "
        "Modify only the application code that the task requires. Never change tests, test "
        "expectations, specs, or dependency declarations. Preserve all existing behavior that "
        "the regression tests cover. Return the complete revised content of exactly one Python "
        "file in a single ```python fenced block, with no prose before or after the block."
    )
    user = f"""# Task
{task_text}

# Applicable specifications
{spec_text}

# Target tests that must pass after the change
{target_tests}

# Regression tests that must keep passing
{regression_tests}

# PowerContext task state
{state_text}

# Current source file
{files_text}

# Baseline test failure before the change
```text
{baseline_log[-8000:]}
```

# Output contract
Return the complete revised content of `ledger/report.py` in one ```python fenced block.
Keep the existing public function name and signature. Do not edit any other file.
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def extract_python(content: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", content, re.DOTALL | re.IGNORECASE)
    candidates = [block.strip("\n") for block in blocks] or [content.strip()]
    for candidate in sorted(candidates, key=len, reverse=True):
        try:
            ast.parse(candidate)
        except SyntaxError:
            continue
        if "def monthly_report" in candidate or "from __future__" in candidate:
            return candidate
    raise MiniMaxError("MiniMax output did not contain a valid Python file")


def _normalize_python(content: str) -> str:
    return content.strip("\n") + "\n"


def assist(
    settings: Settings,
    context: dict,
    task_text: str,
    baseline_log: str,
    evidence_dir: Path,
    max_attempts: int = 2,
) -> dict:
    client = MiniMaxClient(settings)
    attempt_root = evidence_dir / "minimax"
    attempt_root.mkdir(parents=True, exist_ok=True)
    target_paths = sorted({item["path"] for item in context["sources"]["code"]})
    if len(target_paths) != 1:
        raise MiniMaxError(f"expected exactly one target code file, got {target_paths}")
    target_rel = target_paths[0]
    target_file = settings.project_root / target_rel
    original = target_file.read_text(encoding="utf-8")
    original_sha = _sha256_text(original)
    messages = build_messages(settings, context, task_text, baseline_log)
    target_ids = test_node_ids(context, "target")
    regression_ids = test_node_ids(context, "regression")
    if not target_ids:
        raise MiniMaxError("context has no target tests")

    attempts: list[dict] = []
    last_failure = baseline_log
    current = original
    for attempt in range(1, max_attempts + 1):
        directory = attempt_root / f"attempt-{attempt:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        request_meta = {
            "attempt": attempt,
            "created_at": _timestamp(),
            "model": settings.minimax_model,
            "base_url": settings.minimax_base_url,
            "messages": messages,
            "max_tokens": 3000,
            "temperature": 0.1,
            "context_sha256": context["query"]["context_sha256"],
            "group_id_set": bool(settings.minimax_group_id),
        }
        (directory / "request.json").write_text(
            json.dumps(request_meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        response = client.chat(messages)
        (directory / "response.json").write_text(
            json.dumps(
                {
                    "content": response["content"],
                    "usage": response["usage"],
                    "latency_seconds": response["latency_seconds"],
                    "model": response["model"],
                    "created_at": _timestamp(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            candidate = extract_python(response["content"])
        except MiniMaxError as error:
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "invalid-output",
                    "error": str(error),
                    "response_sha256": _sha256_text(response["content"]),
                    "usage": response["usage"],
                }
            )
            messages = messages + [
                {"role": "assistant", "content": response["content"]},
                {
                    "role": "user",
                    "content": "Return the complete revised file in one ```python fenced block, no prose.",
                },
            ]
            continue

        applied = _normalize_python(candidate)
        (directory / "model-output.py").write_text(applied, encoding="utf-8")
        target_file.write_text(applied, encoding="utf-8")
        test_result = run_pytest(
            settings.project_root,
            target_ids + regression_ids,
            directory / "test-output.txt",
        )
        attempts.append(
            {
                "attempt": attempt,
                "status": "tests-passed" if test_result["exit_code"] == 0 else "tests-failed",
                "response_sha256": _sha256_text(response["content"]),
                "output_sha256": _sha256_text(applied),
                "usage": response["usage"],
                "latency_seconds": response["latency_seconds"],
                "exit_code": test_result["exit_code"],
                "test_log": str(directory / "test-output.txt"),
            }
        )
        if test_result["exit_code"] == 0:
            current = applied
            break
        last_failure = test_result["output"]
        messages = messages + [
            {"role": "assistant", "content": response["content"]},
            {
                "role": "user",
                "content": (
                    "The revised file still fails the tests. Fix the implementation and return the "
                    "complete revised file in one ```python fenced block, no prose.\n\n"
                    f"Test output:\n```text\n{last_failure[-6000:]}\n```"
                ),
            },
        ]

    if current == original or attempts[-1]["status"] != "tests-passed":
        target_file.write_text(original, encoding="utf-8")
        raise MiniMaxError(
            "MiniMax did not produce a passing revision; original file restored. "
            f"attempts={json.dumps(attempts, ensure_ascii=False)}"
        )

    after_sha = sha256_file(target_file)
    diff = git_diff(settings.project_root, [target_rel])
    final_dir = attempt_root / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    (final_dir / "context.json").write_text(
        json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (final_dir / "before.py").write_text(original, encoding="utf-8")
    (final_dir / "after.py").write_text(current, encoding="utf-8")
    (final_dir / "diff.patch").write_text(diff, encoding="utf-8")
    record = {
        "schema": "sdd.minimax-record.v1",
        "created_at": _timestamp(),
        "model": settings.minimax_model,
        "base_url": settings.minimax_base_url,
        "group_id_set": bool(settings.minimax_group_id),
        "target_file": target_rel,
        "target_file_before_sha256": original_sha,
        "target_file_after_sha256": after_sha,
        "model_output_sha256": attempts[-1]["output_sha256"],
        "diff_sha256": _sha256_text(diff),
        "context_sha256": context["query"]["context_sha256"],
        "attempts": attempts,
        "test_node_ids": target_ids + regression_ids,
        "request_files": [str(attempt_root / f"attempt-{i:02d}" / "request.json") for i in range(1, len(attempts) + 1)],
        "response_files": [str(attempt_root / f"attempt-{i:02d}" / "response.json") for i in range(1, len(attempts) + 1)],
        "final_diff": str(final_dir / "diff.patch"),
        "final_context": str(final_dir / "context.json"),
    }
    (final_dir / "record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    record["record_path"] = str(final_dir / "record.json")
    return record
