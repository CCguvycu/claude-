"""Filesystem operations, confined to the selected project root."""
from __future__ import annotations

import difflib
import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .safety import check_write_path, resolve_in_root

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
             ".next", ".pytest_cache", ".mypy_cache", "target", ".idea", ".tox"}
BINARY_SNIFF = 4096


@dataclass
class FileEdit:
    path: str          # project-relative
    action: str        # created|modified|deleted
    added: int = 0
    removed: int = 0


def _rel(root: Path, p: Path) -> str:
    return str(p.resolve().relative_to(root.resolve()))


def is_probably_binary(p: Path) -> bool:
    try:
        with p.open("rb") as fh:
            return b"\0" in fh.read(BINARY_SNIFF)
    except OSError:
        return False


def read_file(root: Path, path: str, max_bytes: int = 200_000,
              start_line: int | None = None, end_line: int | None = None) -> str:
    target = resolve_in_root(root, path)
    if not target.exists():
        raise FileNotFoundError(f"{path} does not exist")
    if target.is_dir():
        raise IsADirectoryError(f"{path} is a directory; use list_dir")
    if is_probably_binary(target):
        return f"<binary file, {target.stat().st_size} bytes>"
    text = target.read_text(encoding="utf-8", errors="replace")
    if start_line or end_line:
        lines = text.splitlines()
        s = max(0, (start_line or 1) - 1)
        e = min(len(lines), end_line or len(lines))
        numbered = [f"{i + 1:>6}\t{lines[i]}" for i in range(s, e)]
        return "\n".join(numbered)
    if len(text) > max_bytes:
        return text[:max_bytes] + f"\n<truncated at {max_bytes} bytes of {len(text)}>"
    return text


def list_dir(root: Path, path: str = ".", depth: int = 2, limit: int = 400) -> str:
    base = resolve_in_root(root, path)
    if not base.exists():
        raise FileNotFoundError(f"{path} does not exist")
    if base.is_file():
        return f"{_rel(root, base)} ({base.stat().st_size} bytes)"
    out: list[str] = []
    base_depth = len(base.parts)
    for dirpath, dirnames, filenames in os.walk(base):
        d = Path(dirpath)
        if len(d.parts) - base_depth >= depth:
            dirnames[:] = []
        dirnames[:] = sorted(n for n in dirnames if n not in SKIP_DIRS and not n.startswith(".git"))
        indent = "  " * (len(d.parts) - base_depth)
        if dirpath != str(base):
            out.append(f"{indent}{d.name}/")
        for name in sorted(filenames)[:limit]:
            try:
                size = (d / name).stat().st_size
            except OSError:
                size = 0
            out.append(f"{indent}  {name}  ({size}B)")
        if len(out) > limit:
            out.append(f"... truncated at {limit} entries")
            break
    return "\n".join(out) or "<empty>"


def grep(root: Path, pattern: str, glob: str = "*", max_results: int = 120) -> str:
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid regex: {exc}") from exc
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not fnmatch.fnmatch(name, glob):
                continue
            fp = Path(dirpath) / name
            if is_probably_binary(fp):
                continue
            try:
                for n, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{_rel(root, fp)}:{n}: {line.strip()[:200]}")
                        if len(hits) >= max_results:
                            return "\n".join(hits) + f"\n<stopped at {max_results} matches>"
            except OSError:
                continue
    return "\n".join(hits) or "<no matches>"


def _counts(before: str, after: str) -> tuple[int, int]:
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), n=0))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    return added, removed


def write_file(root: Path, path: str, content: str) -> FileEdit:
    target = check_write_path(root, path)
    existed = target.exists()
    before = target.read_text(encoding="utf-8", errors="replace") if existed else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    added, removed = _counts(before, content)
    return FileEdit(_rel(root, target), "modified" if existed else "created", added, removed)


def edit_file(root: Path, path: str, old: str, new: str, replace_all: bool = False) -> FileEdit:
    target = check_write_path(root, path)
    if not target.exists():
        raise FileNotFoundError(f"{path} does not exist; use write_file to create it")
    before = target.read_text(encoding="utf-8", errors="replace")
    occurrences = before.count(old)
    if occurrences == 0:
        raise ValueError(f"old_text not found in {path}")
    if occurrences > 1 and not replace_all:
        raise ValueError(
            f"old_text appears {occurrences} times in {path}; add more context "
            f"or set replace_all=true")
    after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
    if after == before:
        raise ValueError("edit produced no change")
    target.write_text(after, encoding="utf-8")
    added, removed = _counts(before, after)
    return FileEdit(_rel(root, target), "modified", added, removed)


def delete_file(root: Path, path: str) -> FileEdit:
    target = check_write_path(root, path)
    if not target.exists():
        raise FileNotFoundError(f"{path} does not exist")
    if target.is_dir():
        raise IsADirectoryError(f"{path} is a directory; Forge deletes files only")
    removed = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
    target.unlink()
    return FileEdit(_rel(root, target), "deleted", 0, removed)
