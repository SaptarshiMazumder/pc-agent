"""File tools: read, write, edit.

Mirrors the reference coding tools:
  read  — path, offset (1-indexed), limit; truncation flags
  write — mkdir -p, utf-8 write
  edit  — list of {oldText, newText}; each oldText must match exactly once,
          LF-normalized, non-overlapping; returns a unified diff
"""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path

from . import Tool, ToolResult

MAX_READ_CHARS = 100_000
MAX_LINE_CHARS = 2_000


def _resolve(config, path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path(config.workspace) / p
    return p


class ReadTool(Tool):
    name = "read"
    description = "Read a file's contents. Supports offset (1-indexed line) and limit."
    label = "Read"
    parameters = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "description": "File path (absolute or workspace-relative)."},
            "offset": {"type": "integer", "minimum": 1, "description": "1-indexed start line."},
            "limit": {"type": "integer", "minimum": 1, "description": "Max lines to read."},
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        path = _resolve(self.config, params["path"])
        if not path.is_file():
            return ToolResult.text(f"File not found: {path}", is_error=True)
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.text(f"Cannot read binary file as text: {path}", is_error=True)

        lines = raw.splitlines()
        offset = params.get("offset", 1)
        limit = params.get("limit")
        selected = lines[offset - 1 : offset - 1 + limit] if limit else lines[offset - 1 :]
        truncated_lines = len(selected) < len(lines) - (offset - 1)

        out_lines = []
        for i, line in enumerate(selected, start=offset):
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + "… [line truncated]"
            out_lines.append(f"{i}\t{line}")
        text = "\n".join(out_lines)
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n… [output truncated]"
            truncated_lines = True
        if truncated_lines:
            text += f"\n(showing {len(selected)} of {len(lines)} lines)"
        return ToolResult.text(text if text else "(empty file)")


class WriteTool(Tool):
    name = "write"
    description = "Create or overwrite a file with the given content."
    label = "Write"
    concurrency = "sequential"
    parameters = {
        "type": "object",
        "required": ["path", "content"],
        "properties": {
            "path": {"type": "string", "description": "File path (absolute or workspace-relative)."},
            "content": {"type": "string", "description": "Full file content."},
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        path = _resolve(self.config, params["path"])
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        content = params["content"]
        await asyncio.to_thread(path.write_text, content, "utf-8")
        verb = "Overwrote" if existed else "Created"
        return ToolResult.text(f"{verb} {path} ({len(content.encode('utf-8'))} bytes)")


def apply_edits(original: str, edits: list[dict]) -> tuple[str, str]:
    """Apply [{oldText, newText}] edits. Each oldText must occur exactly once
    (after LF normalization); spans must not overlap. Returns (new_text, diff).
    Raises ValueError with a helpful message otherwise."""
    had_crlf = "\r\n" in original
    text = original.replace("\r\n", "\n")

    spans: list[tuple[int, int, str]] = []
    for i, edit in enumerate(edits):
        old = edit["oldText"].replace("\r\n", "\n")
        new = edit["newText"].replace("\r\n", "\n")
        if not old:
            raise ValueError(f"Edit {i + 1}: oldText must not be empty.")
        count = text.count(old)
        if count == 0:
            close = difflib.get_close_matches(
                old.strip().splitlines()[0] if old.strip() else "", text.splitlines(), n=1, cutoff=0.5
            )
            hint = f" Closest line in file: {close[0]!r}." if close else ""
            raise ValueError(
                f"Edit {i + 1}: oldText not found in file.{hint} "
                f"File starts with: {text[:300]!r}"
            )
        if count > 1:
            raise ValueError(
                f"Edit {i + 1}: oldText matches {count} times; it must be unique. "
                "Add more surrounding context."
            )
        start = text.index(old)
        spans.append((start, start + len(old), new))

    spans.sort(key=lambda s: s[0])
    for (s1, e1, _), (s2, _e2, _) in zip(spans, spans[1:]):
        if s2 < e1:
            raise ValueError("Edits overlap; make them non-overlapping.")

    result = []
    cursor = 0
    for start, end, new in spans:
        result.append(text[cursor:start])
        result.append(new)
        cursor = end
    result.append(text[cursor:])
    new_text = "".join(result)

    diff = "\n".join(
        difflib.unified_diff(
            text.splitlines(), new_text.splitlines(), fromfile="before", tofile="after", lineterm=""
        )
    )
    if had_crlf:
        new_text = new_text.replace("\n", "\r\n")
    return new_text, diff


class EditTool(Tool):
    name = "edit"
    description = "Make precise text replacements in a file. Each oldText must match exactly once."
    label = "Edit"
    concurrency = "sequential"
    parameters = {
        "type": "object",
        "required": ["path", "edits"],
        "properties": {
            "path": {"type": "string", "description": "File path (absolute or workspace-relative)."},
            "edits": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["oldText", "newText"],
                    "properties": {
                        "oldText": {"type": "string", "description": "Exact text to replace (unique in file)."},
                        "newText": {"type": "string", "description": "Replacement text."},
                    },
                },
            },
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        path = _resolve(self.config, params["path"])
        if not path.is_file():
            return ToolResult.text(f"File not found: {path}", is_error=True)
        original = path.read_text(encoding="utf-8")
        try:
            new_text, diff = apply_edits(original, params["edits"])
        except ValueError as e:
            return ToolResult.text(str(e), is_error=True)
        await asyncio.to_thread(path.write_text, new_text, "utf-8")
        return ToolResult.text(f"Edited {path}:\n{diff}")
