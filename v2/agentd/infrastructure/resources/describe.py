"""BasicDescriber — the deterministic, always-on ResourceDescriber.

Sync + dependency-free, so the per-turn manifest can describe files cheaply. It does NOT
hardcode any per-format knowledge — it reads the actual file:
  * images          -> dimensions parsed from the file's own header (generic magic bytes)
  * text-like files -> the first meaningful line (generic: decode-and-read)
  * binary blobs    -> no description (the name + extension + size already say what it is)
Real semantic understanding (a vision caption, an LLM summary) is the manager's pluggable
async ``rich_fn`` — not this cheap fallback.
"""

from __future__ import annotations

import struct
from pathlib import Path

from agentd.domain.resource import KIND_IMAGE
from agentd.infrastructure.documents import extract_text

_COMMENT_PREFIXES = ("#", "//", "--", ";", "%")


def _image_dims(b: bytes) -> tuple[int, int] | None:
    """Width/height straight from the image header — PNG/GIF/BMP/JPEG magic bytes."""
    try:
        if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) >= 24:
            return struct.unpack(">II", b[16:24])
        if b[:6] in (b"GIF87a", b"GIF89a") and len(b) >= 10:
            w, h = struct.unpack("<HH", b[6:10])
            return w, h
        if b[:2] == b"BM" and len(b) >= 26:
            w, h = struct.unpack("<ii", b[18:26])
            return abs(w), abs(h)
        if b[:2] == b"\xff\xd8":  # JPEG: scan SOF markers
            i = 2
            while i + 9 < len(b):
                if b[i] != 0xFF:
                    i += 1
                    continue
                marker = b[i + 1]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", b[i + 5 : i + 9])
                    return w, h
                i += 2 + struct.unpack(">H", b[i + 2 : i + 4])[0]
    except (struct.error, IndexError):
        return None
    return None


def _looks_text(b: bytes) -> bool:
    """Generic binary/text sniff: no NUL byte and decodes as UTF-8."""
    if not b or b"\x00" in b[:1024]:
        return False
    try:
        b[:1024].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _first_line_text(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#!"):
            continue
        if s.startswith(_COMMENT_PREFIXES) or s.startswith('"""') or s.startswith("'''"):
            return s.lstrip("#/-;%\"' ").strip()[:90]
        return s[:90]
    return ""


def _first_line(b: bytes) -> str:
    return _first_line_text(b.decode("utf-8", errors="replace"))


class BasicDescriber:
    def describe(self, kind: str, path: Path, sample: bytes) -> str:
        if kind == KIND_IMAGE:
            fmt = (
                path.suffix.lstrip(".").upper() or "image"
            )  # label derived from the ext, not a table
            dims = _image_dims(sample)
            return f"{fmt} image, {dims[0]}x{dims[1]}" if dims else f"{fmt} image"
        if _looks_text(sample):
            return _first_line(sample)
        text = extract_text(path)  # docx/pdf/xlsx/pptx -> real text
        if text:
            return _first_line_text(text)
        return ""  # opaque binary: name + ext + size say enough
