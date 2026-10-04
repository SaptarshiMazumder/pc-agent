"""The downloader for a ComfyUI connected by its address alone: there is none, so it says what to do.

A rented GPU and a person's own Vast machine have an Instance Portal that runs our downloader on
the box. A plain ComfyUI address has only ComfyUI's own API, which cannot fetch a file, so any
model ComfyUI-Manager will not install has to be put there by the person. This names EVERY such
file of the batch at once — its link and the exact models/ folder it goes in. (Not the portable
installer: that is written only once a workflow compiles, so it never exists while files are missing.)
"""

from __future__ import annotations

from model_download_request import ModelDownloadRequest


class ManualModelDownloader:
    #: ModelInstallationService sends Hugging Face / Civitai files here only when this is True.
    available = False

    def start(self, request: ModelDownloadRequest) -> None:
        """Nothing can fetch it. Not refused here: refusing the first file stopped the batch and
        named only that one — wait() names them all."""

    def status(self, request: ModelDownloadRequest) -> dict | None:
        return None

    def active(self, request: ModelDownloadRequest) -> bool:
        return False

    async def wait(self, requests: list[ModelDownloadRequest], abort, on_update=None) -> None:
        if not requests:
            return
        lines = "\n".join(f"  models/{r.directory}/{r.filename}  <-  {r.origin_url or r.url}" for r in requests)
        raise ValueError(
            "this ComfyUI was connected by its address alone, so nothing here can download onto it, and "
            f"ComfyUI-Manager does not install these {len(requests)} file(s). Tell the user to put each "
            f"in their ComfyUI folder exactly here:\n{lines}\nFiles Manager accepted keep downloading. "
            "Then call comfy_install again to confirm they are loadable."
        )
