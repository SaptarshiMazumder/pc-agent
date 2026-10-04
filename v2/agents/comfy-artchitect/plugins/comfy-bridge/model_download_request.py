"""Validated data shared by the runtime client and the fixed GPU-side worker."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class ModelDownloadRequest:
    filename: str
    url: str
    kind: str
    #: WHO HOSTS THE BYTES — a label for the report and the redirect policy, not a permission.
    #: "civitai" or "huggingface" marks a URL resolved by ModelDownloadSourceResolver:
    #: the GPU receives the signed storage URL and never the platform key.
    source: str = "direct"
    # The provider's permanent link identifies the source; signed CDN URLs rotate.
    origin_url: str = ""

    USER_AGENT = "agentd-model-downloader/1.0"

    @staticmethod
    def headers() -> dict:
        return {"User-Agent": ModelDownloadRequest.USER_AGENT,
                "Accept": "application/octet-stream", "Accept-Encoding": "identity"}

    DIRECTORIES = {
        "checkpoint": "checkpoints", "unet": "diffusion_models",
        "diffusion_model": "diffusion_models", "vae": "vae",
        "text_encoder": "text_encoders", "clip": "text_encoders",
        "lora": "loras", "controlnet": "controlnet", "upscale": "upscale_models",
        "checkpoints": "checkpoints", "diffusion_models": "diffusion_models",
        "text_encoders": "text_encoders", "loras": "loras", "upscale_models": "upscale_models",
    }

    #: Every models/ folder a download may land in: ComfyUI's own (folder_paths) and the ones the
    #: knowledge base's node packs read. A kind is one of these, or a safe subfolder of one
    #: (insightface/models/antelopev2). Anything else is refused — a download never writes outside
    #: models/.
    FOLDERS = frozenset({
        "checkpoints", "diffusion_models", "unet", "vae", "text_encoders", "clip", "loras", "controlnet",
        "upscale_models", "clip_vision", "style_models", "latent_upscale_models", "model_patches",
        "audio_encoders", "embeddings", "hypernetworks", "gligen", "photomaker", "vae_approx",
        "ipadapter", "pulid", "instantid", "insightface", "facexlib", "sam2", "sam3", "detection",
        "FlashVSR", "infinite_you", "geometry_estimation", "xlabs",
    })

    @classmethod
    def folder_of(cls, kind: str) -> str | None:
        """The models/ folder a kind names, or None when it names none."""
        kind = str(kind or "").strip().strip("/")
        if kind in cls.DIRECTORIES:
            return cls.DIRECTORIES[kind]
        parts = kind.split("/")
        if parts[0] in cls.FOLDERS and len(parts) <= 4 and all(re.fullmatch(r"[\w][\w.-]{0,80}", p) for p in parts):
            return kind
        return None

    def __post_init__(self):
        # A MODEL NAME MAY CARRY ITS SUBFOLDER — `Krea2/lora.safetensors` is how a workflow names
        # a file ComfyUI lists from models/loras/Krea2/, and a Windows-made workflow writes it
        # `Krea2\lora.safetensors`. One spelling from here on: forward slashes.
        object.__setattr__(self, "filename", self.filename.replace("\\", "/").strip("/"))
        *folders, base = self.filename.split("/")
        # Direct installs intentionally accept data-only safetensors, not pickle/checkpoint
        # code. Other formats continue through Manager's curated catalogue.
        if (not re.fullmatch(r"[\w][\w .()\[\]-]{0,220}\.safetensors", base)
                or len(self.filename.encode("utf-8")) > 240 or len(folders) > 3
                or any(not re.fullmatch(r"[\w][\w .()\[\]-]{0,100}", f) for f in folders)):
            raise ValueError("GPU direct downloads require a file ending in .safetensors, optionally "
                             "inside a subfolder (Folder/file.safetensors)")
        if self.folder_of(self.kind) is None:
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
    def basename(self) -> str:
        return self.filename.rsplit("/", 1)[-1]

    @property
    def directory(self) -> str:
        return self.folder_of(self.kind) or ""

    @property
    def job_id(self) -> str:
        # Destination, not URL: concurrent chats cannot write the same file twice.
        return hashlib.sha256(f"{self.directory}/{self.filename}".encode()).hexdigest()[:32]

    @property
    def source_id(self) -> str:
        """The FILE, not the spelling of its link: a Hugging Face link with and without
        `?download=true` is one file, and a retry that dropped the switch was refused as
        "another source already owns this destination"."""
        return hashlib.sha256(self.same_file_url(self.origin_url or self.url).encode()).hexdigest()

    #: Query switches that only say "download it" — never which file.
    DOWNLOAD_SWITCHES = frozenset({"download"})

    @classmethod
    def same_file_url(cls, url: str) -> str:
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if k.lower() not in cls.DOWNLOAD_SWITCHES]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def as_dict(self) -> dict:
        return {"filename": self.filename, "url": self.url, "kind": self.kind,
                "source": self.source, "origin_url": self.origin_url}
