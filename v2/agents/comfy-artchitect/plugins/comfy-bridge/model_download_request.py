"""Validated data shared by the runtime client and the fixed GPU-side worker."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class ModelDownloadRequest:
    filename: str
    url: str
    kind: str
    #: WHO HOSTS THE BYTES — a label for the report and the redirect policy, not a permission.
    #: "civitai" marks a URL the runtime resolved from a Civitai download link
    #: (civitai_download_source.py): the GPU sees the signed storage URL, never the link nor
    #: the key that may have been needed to resolve it. Everything else is "direct".
    source: str = "direct"

    DIRECTORIES = {
        "checkpoint": "checkpoints", "unet": "diffusion_models",
        "diffusion_model": "diffusion_models", "vae": "vae",
        "text_encoder": "text_encoders", "clip": "text_encoders",
        "lora": "loras", "controlnet": "controlnet", "upscale": "upscale_models",
        "checkpoints": "checkpoints", "diffusion_models": "diffusion_models",
        "text_encoders": "text_encoders", "loras": "loras", "upscale_models": "upscale_models",
    }

    def __post_init__(self):
        # Direct installs intentionally accept data-only safetensors, not pickle/checkpoint
        # code. Other formats continue through Manager's curated catalogue.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,220}\.safetensors", self.filename):
            raise ValueError("GPU direct downloads require a basename ending in .safetensors")
        if self.kind not in self.DIRECTORIES:
            raise ValueError(f"Unsupported model kind: {self.kind}")
        self._check_url()

    def _check_url(self) -> None:
        """ANY HOST, over TLS. The host allowlist that used to live here (Hugging Face only)
        was opened on purpose: open weights live on Civitai, on mirrors, on a publisher's own
        CDN, and a list of hosts was a list of places the agent could not fetch from. What
        keeps the GPU safe is not the host — it is that the URL carries no credential, walks
        no path, and that the bytes are verified as a real safetensors file before ComfyUI
        ever sees them (GpuModelDownloadWorker.verify). The filename is the request's own, not
        the URL's last segment: a signed storage link rarely ends in the file's name."""
        parsed = urlsplit(self.url)
        path = unquote(parsed.path)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.fragment or not path.strip("/")
            or any(p in (".", "..") for p in path.split("/")) or "\\" in path
        ):
            raise ValueError(
                "Use a direct HTTPS link to the .safetensors file — no credentials in the URL, "
                "no '..' in the path"
            )

    @property
    def directory(self) -> str:
        return self.DIRECTORIES[self.kind]

    @property
    def job_id(self) -> str:
        # Destination, not URL: concurrent chats cannot write the same file twice.
        return hashlib.sha256(f"{self.directory}/{self.filename}".encode()).hexdigest()[:32]

    @property
    def source_id(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()

    def as_dict(self) -> dict:
        return {"filename": self.filename, "url": self.url, "kind": self.kind, "source": self.source}
