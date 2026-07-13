"""File helpers shared by the gateway: MIME/kind classification, a path guard so the
daemon only ever serves files under sanctioned roots, and resolving a tool-declared
output path into a typed artifact.

There is NO inference from text: a file becomes a renderable deliverable only when a
producing tool explicitly declares it (see ToolResult.artifacts). These helpers just turn
that declared path into ``{path, name, mime, kind, size}`` and gate what the /file
endpoint may serve.
"""

from __future__ import annotations

import base64
import binascii
import mimetypes
import re
import uuid
from pathlib import Path

# --- extension -> mime, grouped by how a client should present each kind ------------
_IMAGE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".avif": "image/avif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
_VIDEO = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".mkv": "video/x-matroska",
    ".ogv": "video/ogg",
}
_AUDIO = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
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
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".zip": "application/zip",
    ".puml": "text/plain",
}

# how each family renders on a client: image/video/audio play inline; file = openable chip
_KINDS: tuple[tuple[dict[str, str], str], ...] = (
    (_IMAGE, "image"),
    (_VIDEO, "video"),
    (_AUDIO, "audio"),
    (_DOC, "file"),
)


def classify(path: str | Path) -> tuple[str, str] | None:
    """(kind, mime) for a path with a known media/document extension, else None.
    kind is one of 'image' | 'video' | 'audio' | 'file'."""
    ext = Path(path).suffix.lower()
    for table, kind in _KINDS:
        if ext in table:
            return kind, table[ext]
    return None


def describe_artifact(path: str | Path) -> dict | None:
    """Resolve a tool-declared output PATH into a typed artifact dict
    ``{path, name, mime, kind, size}`` — or None if it isn't a real file. kind comes from
    the extension (image/video/audio) and falls back to 'file' for anything else, so ANY
    deliverable a producing tool declares can be presented (rendered inline, or as an
    openable chip)."""
    p = Path(path)
    try:
        if not p.is_file():
            return None
        size = p.stat().st_size
    except OSError:
        return None
    cls = classify(p)
    kind, mime = cls if cls is not None else ("file", guess_mime(p))
    return {"path": str(p), "name": p.name, "mime": mime, "kind": kind, "size": size}


def resolve_artifacts(paths) -> list[dict]:
    """Resolve a tool's DECLARED output paths into ordered, de-duplicated artifact dicts,
    dropping any that don't exist on disk. This is the one place declared paths become
    presentable deliverables — used by the loop when building a tool result."""
    out: list[dict] = []
    seen: set[str] = set()
    for p in paths or []:
        info = describe_artifact(p)
        if info is None:
            continue
        key = info["path"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(info)
    return out


def guess_mime(path: str | Path) -> str:
    """Best-effort MIME for the /file endpoint: our table first, then stdlib, then a
    safe binary default."""
    ext = Path(path).suffix.lower()
    for table, _ in _KINDS:
        if ext in table:
            return table[ext]
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str) -> str:
    """A filesystem-safe basename for an uploaded file: strip any directory parts and
    replace anything unusual with '-'. Never empty, never traversal."""
    base = Path(name or "").name or "file"
    cleaned = _SAFE_NAME.sub("-", base).strip("-.") or "file"
    return cleaned[:120]


def save_upload(dest_dir: str | Path, name: str, data_b64: str) -> dict:
    """Persist ONE user-uploaded attachment (base64 from a client) under ``dest_dir`` and
    return its typed artifact dict ``{path, name, mime, kind, size}`` — the same shape as a
    tool-declared deliverable, so the client renders it identically and /file streams it.

    The stored file gets a short uuid prefix so two uploads of ``chart.png`` never collide,
    while the artifact keeps the ORIGINAL display name. Raises ValueError on bad base64.
    """
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"invalid base64 for upload {name!r}: {e}") from e
    safe = _safe_name(name)
    d = Path(dest_dir)
    d.mkdir(parents=True, exist_ok=True)
    stored = d / f"{uuid.uuid4().hex[:8]}-{safe}"
    stored.write_bytes(raw)
    cls = classify(stored)
    kind, mime = cls if cls is not None else ("file", guess_mime(stored))
    return {"path": str(stored), "name": safe, "mime": mime, "kind": kind, "size": len(raw)}


def image_data_url(path: str | Path) -> str | None:
    """Read an IMAGE file and return it as a ``data:<mime>;base64,…`` URL for inlining into
    an LLM request (so a vision model can SEE a user-attached image). None if the file is
    missing or isn't a recognised raster/vector image."""
    p = Path(path)
    cls = classify(p)
    if cls is None or cls[0] != "image":
        return None
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    return f"data:{cls[1]};base64,{base64.b64encode(raw).decode('ascii')}"


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
