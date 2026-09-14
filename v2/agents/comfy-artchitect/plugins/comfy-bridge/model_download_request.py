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
        parsed = urlsplit(self.url)
        parts = unquote(parsed.path).split("/")
        if (
            parsed.scheme != "https" or parsed.hostname != "huggingface.co"
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment or len(parts) < 6 or parts[3] != "resolve"
            or any(p in (".", "..", "") for p in parts[1:])
            or "\\" in unquote(parsed.path) or parts[-1] != self.filename
        ):
            raise ValueError("Use the exact public Hugging Face HTTPS /owner/repo/resolve/revision/file URL and filename")

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
        return {"filename": self.filename, "url": self.url, "kind": self.kind}
