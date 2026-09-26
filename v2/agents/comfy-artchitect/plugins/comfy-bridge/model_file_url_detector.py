"""Is this URL a model file? Answered from the URL alone, before anything is fetched.

WHY IT EXISTS. A research or reference fetch reads a page as TEXT. Pointed at a checkpoint, the
same call once started pulling a multi-GB `.safetensors` through the daemon and froze it. The
runtime now refuses a binary body on its own; this is the earlier, clearer refusal: the tool
can say "that is a model file, here is the right call" instead of a transport error.

Model bytes have exactly one route, `comfy_install`, which has the GPU download them. Whether a
file is already there is `comfy_research check=[name]`. Neither ever needs the bytes here.
"""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

#: Weight and archive formats. A URL ending in one of these is never text worth reading.
_WEIGHT_EXTENSIONS = (
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx", ".sft",
    ".zip", ".tar", ".gz", ".7z",
)

#: What a Hugging Face `/resolve/` link may point at and still be read as text.
_TEXT_EXTENSIONS = (".json", ".md", ".txt", ".yaml", ".yml", ".py", ".toml")


class ModelFileUrlDetector:
    """Names the model file a URL points at, or says it points at none."""

    def model_file(self, url: str) -> str:
        """The file name when `url` is a model/weight download, else ''."""
        parts = urlsplit(url.strip())
        path = unquote(parts.path or "")
        name = path.rstrip("/").rsplit("/", 1)[-1]
        lower = name.lower()
        if lower.endswith(_WEIGHT_EXTENSIONS):
            return name
        host = (parts.hostname or "").lower()
        # A Hugging Face `/resolve/` link is a raw file download: text only for text formats.
        if host.endswith("huggingface.co") and "/resolve/" in path and not lower.endswith(_TEXT_EXTENSIONS):
            return name or "a Hugging Face file"
        # Civitai's download API serves the weights themselves, whatever the path says.
        if host.endswith("civitai.com") and path.startswith("/api/download/"):
            return name or "a Civitai model download"
        return ""
