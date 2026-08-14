"""`grep` — search file CONTENTS by regex. The one thing `find` cannot do.

`find` locates files by NAME. Until this existed there was no way to answer "where is this
defined?", "which agents declare an [[mcp]] block?", "who calls this function?" — the questions
that actually come up when working in a codebase. The only route was `find` a plausible set of
files and `read` them one at a time, hoping the answer was in the first few, or shelling out to
`findstr`/`grep` through `exec` and parsing whatever came back.

That gap was expensive in a way that never looked like a failure: an agent that cannot search
answers from the files it happened to open, which reads exactly like an agent that searched and
found nothing.

PURE PYTHON, NO RIPGREP. A shipped tool cannot depend on a binary that may not be installed —
"works on the machine that had rg" is the worst kind of intermittent. This walks the tree itself,
which is fast enough for a source tree and identical everywhere.

THE TENANT FENCE APPLIES. Every path goes through ``_resolve`` (``check_read``) exactly like the
other tools in this plugin: on a hosted daemon a search must not be the one way to read another
account's files. A search that skips the guard is a read that skips the guard.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.write_scope import ReadRefused

from fs_tools import FIND_SKIP_DIRS, _resolve

#: Never worth searching: compiled output, archives, media, and anything else whose "match" is a
#: byte coincidence. Cheaper and more honest than reading them and discarding binary noise.
SKIP_SUFFIXES = frozenset(
    {
        ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a", ".lib",
        ".zip", ".gz", ".tar", ".7z", ".rar", ".whl", ".jar", ".agentpkg",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svg",
        ".mp3", ".mp4", ".wav", ".mov", ".avi", ".pdf", ".woff", ".woff2", ".ttf",
        ".sqlite", ".db", ".lock",
    }
)

#: A single file that is almost certainly generated or vendored. Read caps keep one enormous
#: minified bundle from eating the whole result budget.
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_LINE_CHARS = 400


def _search(
    roots: list[Path],
    pattern: re.Pattern,
    globs: tuple[str, ...],
    max_results: int,
    context: int,
) -> tuple[list[str], int, int]:
    """Walk + match. Returns (rendered lines, files matched, files scanned)."""
    import os

    out: list[str] = []
    files_matched = 0
    scanned = 0
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in FIND_SKIP_DIRS]
            for name in filenames:
                if len(out) >= max_results:
                    return out, files_matched, scanned
                path = Path(dirpath) / name
                if path.suffix.lower() in SKIP_SUFFIXES:
                    continue
                if globs and not any(path.match(g) for g in globs):
                    continue
                try:
                    if path.stat().st_size > MAX_FILE_BYTES:
                        continue
                    # errors="ignore": a stray undecodable byte in an otherwise textual file
                    # must not hide the match — the alternative is skipping the file entirely.
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                scanned += 1
                lines = text.splitlines()
                hits = [i for i, line in enumerate(lines) if pattern.search(line)]
                if not hits:
                    continue
                files_matched += 1
                for i in hits:
                    if len(out) >= max_results:
                        break
                    lo = max(0, i - context)
                    hi = min(len(lines), i + context + 1)
                    for j in range(lo, hi):
                        body = lines[j]
                        if len(body) > MAX_LINE_CHARS:
                            body = body[:MAX_LINE_CHARS] + " …"
                        # ':' marks the match, '-' the context — the grep convention, so the
                        # reader can tell which line actually matched.
                        sep = ":" if j == i else "-"
                        out.append(f"{path}{sep}{j + 1}{sep}{body}")
    return out, files_matched, scanned


class GrepTool(Tool):
    name = "grep"
    default_timeout_sec = 60.0
    default_retryable = True
    default_max_retries = 1
    label = "Grep"
    description = (
        "Search file CONTENTS by regular expression and get back matching lines with their "
        "file and line number. This is how you find where something is DEFINED or USED — "
        "`find` only matches file NAMES. "
        "Use it before reading: one grep beats opening six files to guess which one holds the "
        "answer. Narrow with `path` (a directory) and `glob` (e.g. '*.py', 'agent.toml'), and "
        "`context` for surrounding lines. Case-insensitive with `ignore_case`. Results are "
        "capped, so a broad pattern returns the first matches rather than everything."
    )
    parameters = {
        "type": "object",
        "required": ["pattern"],
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression, e.g. 'def build_\\\\w+' or '\\\\[\\\\[mcp\\\\]\\\\]'.",
            },
            "path": {
                "type": "string",
                "description": "Directory to search (default: the workspace).",
            },
            "glob": {
                "type": "string",
                "description": "Only files matching this pattern, e.g. '*.py' or '*.toml'. "
                "Comma-separate for several.",
            },
            "ignore_case": {"type": "boolean", "description": "Case-insensitive match."},
            "context": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
                "description": "Lines of context around each match (default 0).",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "description": "Cap on returned lines (default 200).",
            },
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        raw = str(params.get("pattern") or "")
        if not raw:
            return ToolResult.text("grep needs a `pattern`", is_error=True)
        flags = re.IGNORECASE if params.get("ignore_case") else 0
        try:
            pattern = re.compile(raw, flags)
        except re.error as e:
            # The regex is the caller's, and a bad one is a caller mistake worth naming
            # precisely — "nothing found" would be a lie about a pattern that never ran.
            return ToolResult.text(f"invalid regular expression {raw!r}: {e}", is_error=True)

        try:
            root = _resolve(self.config, str(params.get("path") or "."))
        except ReadRefused as e:
            return ToolResult.text(str(e), is_error=True)
        if not root.is_dir():
            return ToolResult.text(f"Not a directory: {root}", is_error=True)

        globs = tuple(g.strip() for g in str(params.get("glob") or "").split(",") if g.strip())
        max_results = max(1, int(params.get("max_results") or 200))
        context = max(0, min(10, int(params.get("context") or 0)))

        lines, files_matched, scanned = await asyncio.to_thread(
            _search, [root], pattern, globs, max_results, context
        )
        if not lines:
            where = f"{root}" + (f" ({', '.join(globs)})" if globs else "")
            return ToolResult.text(f"No matches for {raw!r} in {where} — {scanned} file(s) searched.")
        header = (
            f"{len(lines)} line(s) in {files_matched} file(s) for {raw!r} "
            f"({scanned} file(s) searched)"
        )
        if len(lines) >= max_results:
            # Say so. A truncated result that looks complete is how a search convinces someone
            # a thing does not exist.
            header += f" — CAPPED at {max_results}; narrow with `glob`/`path` or raise max_results"
        return ToolResult.text(header + ":\n" + "\n".join(lines))
