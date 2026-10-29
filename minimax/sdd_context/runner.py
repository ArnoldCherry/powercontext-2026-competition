from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_file_hashes(project_root: Path) -> dict[str, str]:
    return {
        path.relative_to(project_root).as_posix(): sha256_file(path)
        for path in sorted((project_root / "tests").rglob("*.py"))
    }


def run_pytest(
    project_root: Path,
    node_ids: list[str],
    output_path: Path | None = None,
    timeout: int = 180,
) -> dict:
    command = [sys.executable, "-m", "pytest", "-q", *node_ids]
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        exit_code = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "") + (error.stderr or "") + f"\nTIMEOUT after {timeout}s\n"
        exit_code = 124
        timed_out = True
    duration = round(time.time() - started, 3)
    text = (
        f"$ {' '.join(command)}\n"
        f"cwd: {project_root}\n"
        f"python: {sys.version.split()[0]}\n"
        f"duration_seconds: {duration}\n"
        f"exit_code: {exit_code}\n\n"
        f"{output}"
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    return {
        "command": command,
        "cwd": str(project_root),
        "python": sys.version.split()[0],
        "duration_seconds": duration,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "output": output,
        "log": str(output_path) if output_path else "",
    }


def failed_node_ids(pytest_output: str) -> set[str]:
    failed: set[str] = set()
    for line in pytest_output.splitlines():
        match = re.match(r"^(?:FAILED|ERROR)\s+(\S+)", line.strip())
        if match:
            failed.add(match.group(1))
    return failed


def passed_count(pytest_output: str) -> int:
    match = re.search(r"(\d+) passed", pytest_output)
    return int(match.group(1)) if match else 0


def failed_count(pytest_output: str) -> int:
    match = re.search(r"(\d+) failed", pytest_output)
    return int(match.group(1)) if match else 0


def error_count(pytest_output: str) -> int:
    match = re.search(r"(\d+) error", pytest_output)
    return int(match.group(1)) if match else 0


def git_info(project_root: Path) -> dict:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=project_root, capture_output=True, text=True
        )
        return (completed.stdout or completed.stderr or "").strip()

    return {
        "head": run("rev-parse", "HEAD"),
        "status_porcelain": run("status", "--porcelain", "--", "."),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
    }


def git_diff(project_root: Path, paths: list[str] | None = None) -> str:
    command = ["git", "diff", "--binary"]
    if paths:
        command.extend(["--", *paths])
    completed = subprocess.run(command, cwd=project_root, capture_output=True, text=True)
    return completed.stdout or ""
