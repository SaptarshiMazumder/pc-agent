"""Resolve provider authentication on the runtime; send only file URLs to the GPU."""

import re
from urllib.parse import parse_qs, urlsplit

from model_download_request import ModelDownloadRequest


class ModelDownloadSourceResolver:
    PROVIDERS = {
        "civitai.com": ("civitai", "CIVITAI_TOKEN"),
        "huggingface.co": ("huggingface", "HF_TOKEN"),
    }
    CIVITAI_QUERY = {"type", "format", "size", "fp", "fileId", "modelVersionId", "quantType"}

    def __init__(self, *, fetch):
        self.fetch = fetch

    def resolve(self, file: dict, timeout_s: float = 30.0) -> dict:
        # Validate before making even the metadata request, including userinfo and port.
        request = ModelDownloadRequest(**file)
        url = request.url
        parsed = urlsplit(url)
        provider = self.PROVIDERS.get(parsed.hostname)
        if provider is None:
            return file  # Public HTTPS downloads never receive either platform key.
        source, secret = provider
        if source == "civitai":
            if (not re.fullmatch(r"/api/download/models/\d+/?", parsed.path)
                    or set(parse_qs(parsed.query, keep_blank_values=True)) - self.CIVITAI_QUERY):
                raise ValueError("Use Civitai's /api/download/models/<version id> download endpoint")
        elif not re.match(r"/[^/]+/[^/]+/resolve/[^/]+/.+", parsed.path):
            raise ValueError("Use Hugging Face's /owner/repo/resolve/revision/file download link")
        headers = ModelDownloadRequest.headers()
        res = self.fetch(url, method="HEAD", headers=headers, timeout_s=timeout_s)
        # Private HF repositories can conceal themselves with 404. Retry only the
        # exact provider origin, never a CDN refusal or a look-alike hostname.
        auth_statuses = (401, 403, 404) if source == "huggingface" else (401, 403)
        auth_attempted = False
        if res.status in auth_statuses and urlsplit(res.url or url).hostname == parsed.hostname:
            auth_attempted = True
            res = self.fetch(url, method="HEAD", timeout_s=timeout_s,
                             headers={**headers, "Authorization": "Bearer ${" + secret + "}"})
        if res.error:
            raise ValueError(f"{source}: transport error resolving the download link; "
                             "no installation confirmed")
        final = str(res.url or url)
        host = urlsplit(final).hostname or parsed.hostname
        if not res.ok:
            auth_note = (f" The runtime tried {secret}; check its validity and this account's file access."
                         if auth_attempted and host == parsed.hostname else
                         " This response does not establish that the provider key is missing or invalid.")
            raise ValueError(f"{request.filename}: HTTP {res.status} from {host} resolving the file.{auth_note}")
        # Validate the final URL too. The key is confined to the broker's request
        # headers; only the resulting signed storage link crosses to the GPU.
        try:
            resolved = ModelDownloadRequest(request.filename, final, request.kind,
                                            source=source, origin_url=url)
        except ValueError as error:
            raise ValueError(f"{source} returned an unsafe download URL: {error}") from error
        if auth_attempted and host == parsed.hostname:
            raise ValueError(f"{source} authorized the file but did not provide a credential-free storage link; "
                             "the platform key cannot be sent to the GPU")
        content_type = str(res.headers.get("content-type", "")).lower()
        if "text/html" in content_type:
            raise ValueError(f"{source} returned a web/login page instead of a model download")
        return resolved.as_dict()
