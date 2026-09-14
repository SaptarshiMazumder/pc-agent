"""Constrain public model redirects without forwarding credentials."""

import urllib.request
from urllib.parse import urlsplit


class ModelDownloadRedirectPolicy(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        host = target.hostname or ""
        if (target.scheme != "https" or target.username or target.password
                or target.port not in (None, 443)
                or not (host == "huggingface.co" or host.endswith(".hf.co")
                        or host.endswith(".huggingface.co"))):
            raise ValueError("Model download redirected outside Hugging Face storage")
        return super().redirect_request(req, fp, code, msg, headers, newurl)
