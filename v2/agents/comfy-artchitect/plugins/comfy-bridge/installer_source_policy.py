"""Portable installer inputs: durable public sources, never platform credentials."""

import re
import ipaddress
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


class InstallerSourcePolicy:
    @staticmethod
    def relative_path(value):
        value = str(value).replace("\\", "/")
        parts = value.split("/")
        if (not value or any(p in ("", ".", "..") for p in parts)
                or any(not re.fullmatch(r"[\w .()\[\]-]+", p) for p in parts)
                or any(p.endswith((".", " ")) for p in parts)
                or any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in parts)):
            raise ValueError("Unsafe installer destination")
        return PurePosixPath(value).as_posix()

    @staticmethod
    def source_url(value):
        """Only allow non-secret selectors; signed links must be resolved on the user's PC."""
        parsed = urlsplit(str(value))
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment or "${" in str(value)
                or any(c.isspace() or ord(c) < 32 for c in str(value))
                or "\\" in unquote(parsed.path)
                or any(p in (".", "..") for p in unquote(parsed.path).split("/"))):
            raise ValueError("Installer requires a credential-free HTTPS source")
        if parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost"):
            raise ValueError("Installer sources must be publicly hosted")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("Installer sources must be publicly hosted")
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if parsed.hostname == "civitai.com":
            if not re.fullmatch(r"/api/download/models/\d+", parsed.path):
                raise ValueError("Use the permanent Civitai model-version download endpoint")
            allowed = {"fileId", "type", "format", "size", "fp"}
        elif parsed.hostname == "huggingface.co":
            if not re.fullmatch(r"/[^/]+/[^/]+/resolve/[^/]+/.+", parsed.path):
                raise ValueError("Use the permanent Hugging Face resolve URL")
            allowed = {"download"}
        else:
            allowed = set()
        # Drop known credentials only on canonical provider endpoints. Never export a CDN
        # signature, or silently turn an arbitrary signed URL into a broken public one.
        for key in list(query):
            if key.lower() in ("token", "api_key", "apikey") and parsed.hostname in ("civitai.com", "huggingface.co"):
                del query[key]
        if any(k not in allowed or not re.fullmatch(r"[\w.-]{1,80}", v) for k, v in query.items()):
            raise ValueError("Source is signed or has unsupported query parameters; record its permanent URL")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))

    @staticmethod
    def repository_url(value):
        value = str(value).removesuffix(".git").rstrip("/")
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
            raise ValueError("Node source must be a credential-free GitHub repository")
        return value
