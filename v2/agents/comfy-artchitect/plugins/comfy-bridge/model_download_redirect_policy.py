"""Constrain model redirects to plain HTTPS, without forwarding credentials."""

import urllib.request
from urllib.parse import urlsplit


class ModelDownloadRedirectPolicy(urllib.request.HTTPRedirectHandler):
    """Where a download may be sent. ANY host, over TLS — the host allowlist that used to live
    here went with the one in model_download_request.py, for the same reason: open weights are
    hosted everywhere, and the safety that matters is that a redirect can never drop to plain
    HTTP, carry credentials, or leave the standard port — and that the bytes are verified as a
    safetensors file afterwards. `source` names where the download started, for the report."""

    def __init__(self, source: str = "direct") -> None:
        super().__init__()
        self.source = source

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (target.scheme != "https" or not target.hostname or target.username
                or target.password or target.port not in (None, 443)):
            raise ValueError("Model download redirected outside plain HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)
