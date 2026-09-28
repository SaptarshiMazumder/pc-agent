"""Resolve provider authentication on the runtime; send only file URLs to the GPU."""

import re
from urllib.parse import parse_qs, urlsplit

from model_download_request import ModelDownloadRequest


class ModelDownloadSourceResolver:
    #: host -> source. Which stored key answers each source is the caller's (`secrets`): the
    #: platform's for its rented GPU, the person's own for their machine.
    PROVIDERS = {"civitai.com": "civitai", "huggingface.co": "huggingface"}
    CIVITAI_QUERY = {"type", "format", "size", "fp", "fileId", "modelVersionId", "quantType"}

    def __init__(self, *, fetch, secrets: dict):
        self.fetch = fetch
        self.secrets = secrets

    @staticmethod
    def _refusal(source: str, secret: str, login_tried: bool) -> str:
        """What a refused download means, in words the agent can pass on. The code cannot see
        whether the key is set (the host holds it), so it says what to check, not a guess."""
        if not login_tried:
            return " This response does not establish that the provider key is missing or invalid."
        site = "Hugging Face" if source == "huggingface" else "Civitai"
        if secret.startswith("USER_"):
            return (f" The runtime tried {secret}. The file needs a {site} login, and on the user's own machine that is THEIR "
                    f"{site} token: ask them to add it in Workspace → Connection → Keys (if they have "
                    f"not) and to accept the model's licence on its {site} page with that account. "
                    "Or find the same file on a mirror that needs no login.")
        return (f" The runtime tried {secret}. The file needs a {site} login and the platform's account has no access to it "
                f"(its licence may not be accepted). Pick another source or another model.")

    def _hf_repo_exists(self, path: str, timeout_s: float) -> bool:
        """HUGGING FACE ANSWERS 401 FOR A REPO THAT DOES NOT EXIST, as it does for one that needs
        a login — so a made-up link read as "the platform has no access". Its public model API
        tells them apart without any key: a real repo, gated or not, answers 200; a missing or
        private one does not. Unknown (a transport error) counts as existing, so the login
        message stays what it was."""
        owner_repo = "/".join(path.split("/")[1:3])
        res = self.fetch(f"https://huggingface.co/api/models/{owner_repo}", method="GET",
                         headers={"User-Agent": ModelDownloadRequest.USER_AGENT}, timeout_s=timeout_s)
        return bool(res.error) or res.ok

    def resolve(self, file: dict, timeout_s: float = 30.0) -> dict:
        # Validate before making even the metadata request, including userinfo and port.
        request = ModelDownloadRequest(**file)
        url = request.url
        parsed = urlsplit(url)
        source = self.PROVIDERS.get(parsed.hostname)
        if source is None:
            return file  # Public HTTPS downloads never receive either platform key.
        secret = self.secrets[source]
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
            if source == "huggingface" and host == parsed.hostname and not self._hf_repo_exists(parsed.path, timeout_s):
                raise ValueError(
                    f"{request.filename}: the Hugging Face repository in this link does not exist or is "
                    f"private ({'/'.join(parsed.path.split('/')[1:3])}) — the link is wrong, not the login. "
                    "Use the link the workflow's setup notes or its installer give; never build one."
                )
            raise ValueError(f"{request.filename}: HTTP {res.status} from {host} resolving the file."
                             + self._refusal(source, secret, auth_attempted and host == parsed.hostname))
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
