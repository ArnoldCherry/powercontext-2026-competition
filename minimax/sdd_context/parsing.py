from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SPEC_HEADING = re.compile(r"^##\s+(REQ-[A-Za-z0-9][A-Za-z0-9-]*)\s+(.+?)\s*$")
MARKER_NAMES = {"pytest.mark.sdd", "mark.sdd", "sdd"}


@dataclass
class TaskDoc:
    task_id: str
    title: str
    body: str
    path: Path
    sha256: str

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}".strip()


@dataclass
class SpecEntry:
    doc_path: str
    req_id: str
    title: str
    body: str
    source: str
    line_start: int
    line_end: int
    sha256: str


@dataclass
class CodeSymbol:
    path: str
    qualname: str
    kind: str
    line_start: int
    line_end: int
    signature: str
    docstring: str
    source: str
    sha256: str


@dataclass
class TestCase:
    path: str
    name: str
    line_start: int
    line_end: int
    markers: list[dict]
    docstring: str
    source: str
    sha256: str


@dataclass
class ParsedProject:
    specs: list[SpecEntry] = field(default_factory=list)
    code_symbols: list[CodeSymbol] = field(default_factory=list)
    tests: list[TestCase] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    trace: dict = field(default_factory=dict)

    def symbol_index(self) -> dict[tuple[str, str], CodeSymbol]:
        return {(symbol.path, symbol.qualname): symbol for symbol in self.code_symbols}

    def test_index(self) -> dict[tuple[str, str], TestCase]:
        return {(test.path, test.name): test for test in self.tests}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def parse_task(path: str | Path) -> TaskDoc:
    task_path = Path(path).resolve()
    text = task_path.read_text(encoding="utf-8")
    metadata: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        lines = text.splitlines()
        end = None
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                end = index
                break
        if end is None:
            raise ValueError(f"{task_path}: unterminated front matter")
        for line in lines[1:end]:
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()
        body = "\n".join(lines[end + 1 :]).strip()
    title = metadata.get("title", task_path.stem)
    task_id = metadata.get("task_id", task_path.stem)
    return TaskDoc(
        task_id=task_id,
        title=title,
        body=body,
        path=task_path,
        sha256=sha256_file(task_path),
    )


def parse_spec_file(path: Path, project_root: Path) -> list[SpecEntry]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = SPEC_HEADING.match(line.rstrip("\n"))
        if match:
            headings.append((index, match))
    if not headings:
        return []

    entries: list[SpecEntry] = []
    seen: set[str] = set()
    for position, (index, match) in enumerate(headings):
        end_index = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        entry_lines = lines[index:end_index]
        while entry_lines and not entry_lines[-1].strip():
            entry_lines.pop()
        source = "".join(entry_lines).rstrip("\n")
        body = "\n".join(line.rstrip("\n") for line in entry_lines[1:]).strip("\n")
        req_id = match.group(1)
        if req_id in seen:
            raise ValueError(f"{path}: duplicate requirement {req_id}")
        seen.add(req_id)
        entries.append(
            SpecEntry(
                doc_path=_relative(path, project_root),
                req_id=req_id,
                title=match.group(2).strip(),
                body=body,
                source=source,
                line_start=index + 1,
                line_end=index + len(entry_lines),
                sha256=sha256_text(source),
            )
        )
    return entries


def _node_source(lines: list[str], node: ast.AST) -> str:
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start)
    return "".join(lines[start - 1 : end]).rstrip("\n")


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(base) for base in node.bases]
        return f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    signature = f"{prefix} {node.name}({ast.unparse(node.args)})"
    if node.returns is not None:
        signature += f" -> {ast.unparse(node.returns)}"
    return signature


def _literal_or_source(node: ast.AST) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return ast.unparse(node)


def parse_markers(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict]:
    markers: list[dict] = []
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call):
            markers.append(
                {
                    "name": ast.unparse(decorator.func),
                    "args": [_literal_or_source(arg) for arg in decorator.args],
                    "kwargs": {
                        keyword.arg: _literal_or_source(keyword.value)
                        for keyword in decorator.keywords
                        if keyword.arg
                    },
                }
            )
        else:
            markers.append({"name": ast.unparse(decorator), "args": [], "kwargs": {}})
    return markers


def sdd_markers(markers: list[dict]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for marker in markers:
        name = str(marker.get("name", ""))
        if name not in MARKER_NAMES and not name.endswith(".sdd"):
            continue
        args = marker.get("args") or []
        if not args:
            continue
        req_id = str(args[0])
        kind = str(marker.get("kwargs", {}).get("kind") or (args[1] if len(args) > 1 else ""))
        pairs.append((req_id, kind))
    return pairs


def parse_python_file(
    path: Path, project_root: Path, test_file: bool
) -> tuple[list[CodeSymbol], list[TestCase]]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    relative = _relative(path, project_root)
    symbols: list[CodeSymbol] = []
    tests: list[TestCase] = []

    def visit(node: ast.AST, prefix: str = "") -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{prefix}{node.name}"
            is_test = test_file and not prefix and node.name.startswith("test_")
            if is_test:
                markers = parse_markers(node)
                source = _node_source(lines, node)
                tests.append(
                    TestCase(
                        path=relative,
                        name=node.name,
                        line_start=node.lineno,
                        line_end=node.end_lineno or node.lineno,
                        markers=markers,
                        docstring=ast.get_docstring(node) or "",
                        source=source,
                        sha256=sha256_text(source),
                    )
                )
            elif not test_file:
                source = _node_source(lines, node)
                symbols.append(
                    CodeSymbol(
                        path=relative,
                        qualname=qualname,
                        kind="method" if prefix else (
                            "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                        ),
                        line_start=node.lineno,
                        line_end=node.end_lineno or node.lineno,
                        signature=_signature(node),
                        docstring=ast.get_docstring(node) or "",
                        source=source,
                        sha256=sha256_text(source),
                    )
                )
            return
        if isinstance(node, ast.ClassDef):
            if not test_file:
                source = _node_source(lines, node)
                symbols.append(
                    CodeSymbol(
                        path=relative,
                        qualname=f"{prefix}{node.name}",
                        kind="class",
                        line_start=node.lineno,
                        line_end=node.end_lineno or node.lineno,
                        signature=_signature(node),
                        docstring=ast.get_docstring(node) or "",
                        source=source,
                        sha256=sha256_text(source),
                    )
                )
            for child in node.body:
                visit(child, f"{prefix}{node.name}.")

    for node in tree.body:
        visit(node)
    return symbols, tests


def parse_trace(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "sdd-traceability/v1":
        raise ValueError(f"{path}: unsupported trace schema {data.get('schema')!r}")
    if not isinstance(data.get("requirements"), dict):
        raise ValueError(f"{path}: requirements must be an object")
    return data


def discover_project(project_root: str | Path) -> ParsedProject:
    root = Path(project_root).resolve()
    parsed = ParsedProject()
    documents: list[dict] = []

    for path in sorted((root / "specs").glob("*.md")):
        entries = parse_spec_file(path, root)
        parsed.specs.extend(entries)
        documents.append({"path": _relative(path, root), "kind": "spec", "sha256": sha256_file(path)})

    trace_path = root / "specs" / "traceability.json"
    parsed.trace = parse_trace(trace_path)
    documents.append(
        {"path": _relative(trace_path, root), "kind": "trace", "sha256": sha256_file(trace_path)}
    )

    for path in sorted((root / "ledger").rglob("*.py")):
        symbols, _ = parse_python_file(path, root, test_file=False)
        parsed.code_symbols.extend(symbols)
        documents.append({"path": _relative(path, root), "kind": "code", "sha256": sha256_file(path)})

    for path in sorted((root / "tests").rglob("test_*.py")):
        _, tests = parse_python_file(path, root, test_file=True)
        parsed.tests.extend(tests)
        documents.append({"path": _relative(path, root), "kind": "test", "sha256": sha256_file(path)})

    parsed.documents = documents
    return parsed
