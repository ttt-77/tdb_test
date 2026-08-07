"""Sandboxed workspace tools for the agent.

The agent gets a directory containing the SAP text and must write its answers
there as files. Every path is confined to the workspace root — traversal
outside it is rejected.

NOTE: `run_r` executes model-generated code. It is intended to run inside an
ephemeral, isolated container (HF Space) with a timeout.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

MAX_READ_LINES = 400
MAX_GREP_MATCHES = 60
MAX_WRITE_BYTES = 2_000_000
R_TIMEOUT_SEC = 120


class Workspace:
    """Filesystem operations confined to `root`."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- path safety ------------------------------------------------------
    def _resolve(self, rel: str) -> Path:
        p = (self.root / (rel or "").lstrip("/")).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"path escapes the workspace: {rel}")
        return p

    def rel(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    # -- tools ------------------------------------------------------------
    def list_files(self) -> str:
        out = []
        for p in sorted(self.root.rglob("*")):
            if p.is_file():
                out.append(f"{self.rel(p)}  ({p.stat().st_size:,} bytes)")
        return "\n".join(out) or "(workspace is empty)"

    def read_file(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> str:
        p = self._resolve(path)
        if not p.is_file():
            return f"ERROR: no such file: {path}"
        limit = max(1, min(int(limit or MAX_READ_LINES), MAX_READ_LINES))
        offset = max(1, int(offset or 1))
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        chunk = lines[offset - 1 : offset - 1 + limit]
        if not chunk:
            return f"(no lines at offset {offset}; file has {total} lines)"
        body = "\n".join(f"{offset + i}\t{ln}" for i, ln in enumerate(chunk))
        end = offset + len(chunk) - 1
        more = (
            f"\n\n[showing lines {offset}-{end} of {total}. "
            f"Use offset={end + 1} to continue.]"
            if end < total
            else f"\n\n[end of file, {total} lines]"
        )
        return body + more

    def grep(self, pattern: str, path: str = "", max_matches: int = MAX_GREP_MATCHES) -> str:
        max_matches = max(1, min(int(max_matches or MAX_GREP_MATCHES), MAX_GREP_MATCHES))
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"ERROR: bad regex: {e}"
        targets: List[Path] = []
        if path:
            p = self._resolve(path)
            if not p.is_file():
                return f"ERROR: no such file: {path}"
            targets = [p]
        else:
            targets = [p for p in sorted(self.root.rglob("*")) if p.is_file()]

        hits: List[str] = []
        for p in targets:
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for i, ln in enumerate(lines, start=1):
                if rx.search(ln):
                    hits.append(f"{self.rel(p)}:{i}\t{ln.strip()[:300]}")
                    if len(hits) >= max_matches:
                        return "\n".join(hits) + f"\n\n[stopped at {max_matches} matches]"
        return "\n".join(hits) or f"(no matches for /{pattern}/)"

    def write_file(self, path: str, content: str) -> str:
        p = self._resolve(path)
        data = (content or "").encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            return f"ERROR: content too large ({len(data):,} bytes)"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content or "", encoding="utf-8")
        return f"wrote {self.rel(p)} ({len(data):,} bytes)"

    def run_r(self, path: str) -> str:
        p = self._resolve(path)
        if not p.is_file():
            return f"ERROR: no such file: {path}"
        exe = shutil.which("Rscript")
        if not exe:
            return (
                "ERROR: Rscript is not available in this environment, so the R "
                "script could not be executed. Continue without running it."
            )
        try:
            proc = subprocess.run(
                [exe, "--vanilla", str(p)],
                capture_output=True,
                text=True,
                timeout=R_TIMEOUT_SEC,
                cwd=str(self.root),
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: Rscript timed out after {R_TIMEOUT_SEC}s"
        parts = [f"exit code: {proc.returncode}"]
        if proc.stdout.strip():
            parts.append("--- stdout ---\n" + proc.stdout.strip()[:6000])
        if proc.stderr.strip():
            parts.append("--- stderr ---\n" + proc.stderr.strip()[:4000])
        return "\n".join(parts)

    # -- dispatch ---------------------------------------------------------
    def call(self, name: str, args: Dict[str, Any]) -> str:
        try:
            if name == "list_files":
                return self.list_files()
            if name == "read_file":
                return self.read_file(
                    args.get("path", ""), args.get("offset", 1), args.get("limit", MAX_READ_LINES)
                )
            if name == "grep":
                return self.grep(
                    args.get("pattern", ""), args.get("path", ""), args.get("max_matches", MAX_GREP_MATCHES)
                )
            if name == "write_file":
                return self.write_file(args.get("path", ""), args.get("content", ""))
            if name == "run_r":
                return self.run_r(args.get("path", "output.R"))
            return f"ERROR: unknown tool {name}"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "list_files",
        "description": "List all files in the workspace with their sizes.",
        "properties": {},
        "required": [],
    },
    {
        "name": "read_file",
        "description": (
            "Read a slice of a text file with line numbers. The SAP is large, so "
            "read it in chunks (or grep first to find the right lines)."
        ),
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative path, e.g. sap.txt"},
            "offset": {"type": "integer", "description": "1-based first line to read (default 1)"},
            "limit": {"type": "integer", "description": f"Lines to read (max {MAX_READ_LINES})"},
        },
        "required": ["path"],
    },
    {
        "name": "grep",
        "description": (
            "Case-insensitive regex search across the workspace (or one file). "
            "Returns path:line and the matching text — use it to locate sections, "
            "then read_file around those line numbers."
        ),
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression"},
            "path": {"type": "string", "description": "Optional single file to search"},
            "max_matches": {"type": "integer", "description": f"Max matches (default {MAX_GREP_MATCHES})"},
        },
        "required": ["pattern"],
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file in the workspace with the given content.",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative path, e.g. output.json"},
            "content": {"type": "string", "description": "Full file content"},
        },
        "required": ["path", "content"],
    },
    {
        "name": "run_r",
        "description": (
            "Execute an R script with Rscript and return its exit code, stdout and "
            "stderr. Use it to verify output.R runs and prints the values you "
            "reported; fix the script and re-run if it errors or disagrees."
        ),
        "properties": {
            "path": {"type": "string", "description": "Script path (default output.R)"},
        },
        "required": [],
    },
]


def anthropic_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": {
                "type": "object",
                "properties": t["properties"],
                "required": t["required"],
            },
        }
        for t in _TOOLS
    ]


def openai_tools() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": {
                    "type": "object",
                    "properties": t["properties"],
                    "required": t["required"],
                },
            },
        }
        for t in _TOOLS
    ]
