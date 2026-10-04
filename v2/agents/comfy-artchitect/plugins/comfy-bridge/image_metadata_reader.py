"""ImageMetadataReader — the generation record an image file carries, as text, from its bytes.

Where each producer writes it (comfy_kb/lora_research.md §7, 39 Civitai originals):
  * ComfyUI SaveImage — PNG text chunks `prompt` (the API graph) and `workflow` (the editor graph);
  * A1111 / Forge — PNG text chunk `parameters` (prompt, "Negative prompt:", "Steps: …" lines);
  * Civitai's own generator — EXIF UserComment (JPEG or PNG): an 8-byte charset header, then
    UTF-16 text — A1111-style lines or a ComfyUI API graph.

NEVER TRUST THE NAME: Civitai serves PNGs under `.jpeg` URLs (27763004), so the format is the
bytes' magic. Pure: bytes in, {key: text} out — no image library, nothing read or written.
"""

from __future__ import annotations

import struct
import zlib

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
EXIF_IFD = 0x8769
USER_COMMENT = 0x9286


class ImageMetadataReader:
    @staticmethod
    def format_of(data: bytes) -> str:
        if data.startswith(PNG_MAGIC):
            return "png"
        if data[:3] == b"\xff\xd8\xff":
            return "jpeg"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp"
        return ""

    @classmethod
    def read(cls, data: bytes) -> dict[str, str]:
        """{key: text}: a PNG's text chunks, and `UserComment` from EXIF wherever it is."""
        fmt = cls.format_of(data)
        out: dict[str, str] = {}
        exif = b""
        if fmt == "png":
            out, exif = cls._png(data)
        elif fmt == "jpeg":
            exif = cls._jpeg_exif(data)
        elif fmt == "webp":
            exif = cls._webp_exif(data)
        comment = cls._user_comment(exif) if exif else ""
        if comment:
            out["UserComment"] = comment
        return out

    # ------------------------------------------------------------------ PNG

    @staticmethod
    def _png(data: bytes) -> tuple[dict[str, str], bytes]:
        out: dict[str, str] = {}
        exif = b""
        pos = len(PNG_MAGIC)
        while pos + 8 <= len(data):
            length, kind = struct.unpack(">I4s", data[pos:pos + 8])
            body = data[pos + 8:pos + 8 + length]
            pos += 12 + length
            if kind == b"tEXt" and b"\0" in body:
                key, text = body.split(b"\0", 1)
                out[key.decode("latin-1")] = text.decode("latin-1")
            elif kind == b"zTXt" and b"\0" in body:
                key, rest = body.split(b"\0", 1)
                out[key.decode("latin-1")] = zlib.decompress(rest[1:]).decode("latin-1")
            elif kind == b"iTXt" and b"\0" in body:
                key, rest = body.split(b"\0", 1)
                compressed = rest[:1] == b"\1"  # then the method byte, a language tag, a translated key
                text = rest[2:].split(b"\0", 2)[-1]
                out[key.decode("latin-1")] = (zlib.decompress(text) if compressed else text).decode("utf-8", "replace")
            elif kind == b"eXIf":
                exif = body
            elif kind == b"IEND":
                break
        return out, exif

    # ------------------------------------------------------------------ JPEG / WebP EXIF

    @staticmethod
    def _jpeg_exif(data: bytes) -> bytes:
        pos = 2
        while pos + 4 <= len(data) and data[pos] == 0xFF:
            marker = data[pos + 1]
            if marker in (0xD9, 0xDA):  # end of image / start of scan: no metadata past here
                break
            length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
            body = data[pos + 4:pos + 2 + length]
            if marker == 0xE1 and body.startswith(b"Exif\0\0"):
                return body[6:]
            pos += 2 + length
        return b""

    @staticmethod
    def _webp_exif(data: bytes) -> bytes:
        pos = 12
        while pos + 8 <= len(data):
            kind, length = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
            if kind == b"EXIF":
                body = data[pos + 8:pos + 8 + length]
                return body[6:] if body.startswith(b"Exif\0\0") else body
            pos += 8 + length + (length & 1)
        return b""

    @classmethod
    def _user_comment(cls, tiff: bytes) -> str:
        """EXIF UserComment from a TIFF block: IFD0 → the Exif IFD → tag 0x9286."""
        if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
            return ""
        end = "<" if tiff[:2] == b"II" else ">"
        ifd0 = struct.unpack(end + "I", tiff[4:8])[0]
        exif_ifd = cls._tag(tiff, end, ifd0, EXIF_IFD)
        raw = cls._tag(tiff, end, exif_ifd, USER_COMMENT, as_bytes=True) if exif_ifd is not None else None
        if not isinstance(raw, bytes) or len(raw) < 8:
            return ""
        head, body = raw[:8], raw[8:]
        if head.startswith(b"UNICODE"):
            # Civitai writes big-endian; an ASCII-heavy text then starts with a zero byte.
            return body.decode("utf-16-be" if body[:1] == b"\0" else "utf-16-le", "replace").rstrip("\0")
        return body.decode("utf-8", "replace").rstrip("\0")

    @staticmethod
    def _tag(tiff: bytes, end: str, offset: int, tag: int, as_bytes: bool = False):
        """One tag's value from the IFD at `offset`: an int for a LONG, bytes for an UNDEFINED."""
        if offset + 2 > len(tiff):
            return None
        count = struct.unpack(end + "H", tiff[offset:offset + 2])[0]
        for i in range(count):
            e = offset + 2 + 12 * i
            if e + 12 > len(tiff):
                return None
            t, kind, n = struct.unpack(end + "HHI", tiff[e:e + 8])
            if t != tag:
                continue
            if not as_bytes:
                return struct.unpack(end + "I", tiff[e + 8:e + 12])[0]
            start = struct.unpack(end + "I", tiff[e + 8:e + 12])[0] if n > 4 else e + 8
            return tiff[start:start + n]
        return None


__all__ = ["ImageMetadataReader"]
