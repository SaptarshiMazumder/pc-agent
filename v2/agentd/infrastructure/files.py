"""File helpers shared by the gateway: MIME/kind classification, a path guard so the
daemon only ever serves files under sanctioned roots, and artifact extraction from a
run's text.

Detection is SERVER-SIDE and deterministic: an "artifact" is simply a real file, under
an allowed root, with a known extension, whose absolute path appears in a tool result or
the assistant's text. There is nothing to persist — the same set is derived on the fly
for a live run and for history replay, so every client (desktop, terminal, future web)
renders identical media without any client-side heuristics.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

# --- extension -> mime, grouped by how a client should present each kind ------------
_IMAGE = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".bmp": "image/bmp", ".ico": "image/x-icon", ".avif": "image/avif",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}
_VIDEO = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".m4v": "video/x-m4v", ".mkv": "video/x-matroska", ".ogv": "video/ogg",
}
_AUDIO = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".m4a": "audio/mp4", ".flac": "audio/flac", ".aac": "audio/aac",
}
# recognised documents — not inline-renderable in a webview, shown as an openable chip
_DOC = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".json": "application/json", ".txt": "text/plain",
    ".md": "text/markdown", ".html": "text/html", ".htm": "text/html",
    ".zip": "application/zip", ".puml": "text/plain",
}

# how each family renders on a client: image/video/audio play inline; file = openable chip
_KINDS: tuple[tuple[dict[str, str], str], ...] = (
    (_IMAGE, "image"), (_VIDEO, "video"), (_AUDIO, "audio"), (_DOC, "file"),
)
# union of every extension we auto-detect as an artifact (rendering only — the /file
# endpoint itself will serve ANY file under an allowed root)
_KNOWN_EXT = {ext for table, _ in _KINDS for ext in table}


def classify(path: str | Path) -> tuple[str, str] | None:
    """(kind, mime) for a path with a known media/document extension, else None.
    kind is one of 'image' | 'video' | 'audio' | 'file'."""
    ext = Path(path).suffix.lower()
    for table, kind in _KINDS:
        if ext in table:
            return kind, table[ext]
    return None


def guess_mime(path: str | Path) -> str:
    """Best-effort MIME for the /file endpoint: our table first, then stdlib, then a
    safe binary default."""
    ext = Path(path).suffix.lower()
    for table, _ in _KINDS:
        if ext in table:
            return table[ext]
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def is_under_roots(path: str | Path, roots: list[Path]) -> bool:
    """True if ``path`` resolves to a location inside one of ``roots`` (symlinks and
    ``..`` resolved first, so the guard can't be walked out of)."""
    try:
        rp = Path(path).resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    for root in roots:
        try:
            rp.relative_to(Path(root).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


# absolute path ending in a known extension: a Windows drive path (``C:\...``) or a
# POSIX/UNC path (``/...`` or ``\\...``). Lazy so it stops at the FIRST matching
# extension; the lookahead lets a path contain spaces yet end cleanly at whitespace,
# quotes or common punctuation.
_EXT_ALT = "|".join(re.escape(e[1:]) for e in sorted(_KNOWN_EXT, key=len, reverse=True))
_PATH_RE = re.compile(
    r"""(?P<path>(?:[A-Za-z]:[\\/]|\\\\|/)[^\r\n"'`<>|?*]*?\.(?:%s))(?=$|["'`\s<>)\]},;])"""
    % _EXT_ALT,
    re.IGNORECASE,
)


def extract_artifacts(text: str, roots: list[Path]) -> list[dict]:
    """Ordered, de-duplicated artifacts referenced in ``text`` that actually exist on
    disk under an allowed root. Each: {path, name, mime, kind, size}."""
    if not text:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for m in _PATH_RE.finditer(text):
        raw = m.group("path").strip().strip("\"'`")
        cls = classify(raw)
        if cls is None:
            continue
        p = Path(raw)
        try:
            if not p.is_file() or not is_under_roots(p, roots):
                continue
            key = str(p.resolve()).lower()
        except (OSError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        kind, mime = cls
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        out.append({"path": str(p), "name": p.name, "mime": mime, "kind": kind, "size": size})
    return out
