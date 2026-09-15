"""A Civitai download link, turned into something the GPU can fetch without a key.

WHY THE RUNTIME RESOLVES IT. Civitai's download endpoint answers with a redirect to a signed,
time-limited URL on its own storage — and many files need the platform's Civitai API key to get
that redirect. The key is the platform's, substituted into the request by the sandbox broker on
this side, and it must never travel to the rented machine: the person has a terminal there. So
this side asks Civitai where the bytes are (a HEAD, no body), and hands the GPU the signed
storage URL, which needs no credential at all. The model bytes still never touch the runtime.

BARE FIRST, KEY ON REFUSAL — the same rule comfy_research follows: an unset secret would
otherwise poison every public request with a literal `${NAME}` header.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

#: The one link shape accepted from the model: the version's download endpoint.
_DOWNLOAD_PATH = re.compile(r"^/api/download/models/\d+/?$")
#: Query keys Civitai's own download buttons put on that link. Anything else is refused: the
#: link is what the GPU is told to trust, so it is not a free-form URL.
_ALLOWED_QUERY = {"type", "format", "size", "fp", "fileId", "modelVersionId"}
_TOKEN = "${CIVITAI_TOKEN}"


def is_civitai_download(url: str) -> bool:
    p = urlsplit(str(url or ""))
    return p.scheme == "https" and p.hostname == "civitai.com" and bool(_DOWNLOAD_PATH.match(p.path))


def _check_link(url: str) -> None:
    p = urlsplit(url)
    if (
        p.scheme != "https" or p.hostname != "civitai.com" or p.username or p.password
        or p.port not in (None, 443) or p.fragment or not _DOWNLOAD_PATH.match(p.path)
        or set(parse_qs(p.query, keep_blank_values=True)) - _ALLOWED_QUERY
    ):
        raise ValueError(
            "A Civitai link must be the version's download endpoint, "
            "https://civitai.com/api/download/models/<version id> (its own ?type=/format= query "
            "is fine). Take it from the model page's Download button, not from a search summary."
        )


def _over_tls(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme == "https" and bool(p.hostname) and not p.username and not p.password


def resolve(file: dict, fetch, timeout_s: float = 30.0) -> dict:
    """`{filename, url, kind}` with a Civitai link -> the same entry pointing at the signed storage
    URL Civitai redirects to, marked `source: "civitai"`. Any other entry comes back unchanged.
    Raises ValueError with the reason when Civitai will not hand the file over."""
    url = str(file.get("url") or "")
    if not is_civitai_download(url):
        return file
    _check_link(url)
    res = fetch(url, method="HEAD", timeout_s=timeout_s)
    if res.status in (401, 403):
        res = fetch(url, method="HEAD", headers={"Authorization": f"Bearer {_TOKEN}"}, timeout_s=timeout_s)
    if res.error:
        raise ValueError(f"Civitai could not be reached for {file.get('filename')}: {res.error}")
    if res.status in (401, 403):
        raise ValueError(
            f"Civitai refuses to hand over {file.get('filename')} (HTTP {res.status}): this file "
            "needs a Civitai API key with access to it, and the platform's key was not accepted."
        )
    if not res.ok:
        raise ValueError(f"Civitai answered HTTP {res.status} for {file.get('filename')}; the link may be wrong or the file removed.")
    final = str(res.url or url)
    if not _over_tls(final):
        raise ValueError(f"Civitai sent {file.get('filename')} to a non-HTTPS or credentialed URL; refusing to download from there.")
    return {**file, "url": final, "source": "civitai"}
