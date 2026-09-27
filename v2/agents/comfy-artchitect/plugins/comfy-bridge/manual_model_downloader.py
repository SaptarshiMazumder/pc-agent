"""The downloader for a ComfyUI connected by its address alone: there is none, so it says what to do.

A rented GPU and a person's own Vast machine have an Instance Portal that runs our downloader on
the box. A plain ComfyUI address has only ComfyUI's own API, which cannot fetch a file, so any
model ComfyUI-Manager will not install has to be put there by the person. This names the file,
where it goes, and the installer script that does it for them.
"""

from __future__ import annotations

from model_download_request import ModelDownloadRequest


class ManualModelDownloader:
    #: ModelInstallationService sends Hugging Face / Civitai files here only when this is True.
    available = False

    def start(self, request: ModelDownloadRequest) -> None:
        raise ValueError(
            f"{request.filename} cannot be downloaded onto this ComfyUI: it was connected by its "
            "address alone, and Manager does not install this file. Tell the user to download "
            f"{request.origin_url or request.url} into their ComfyUI's models/{request.kind} "
            "folder (or run the workflow's install_<name>.py on that machine, which fetches every "
            "file it needs), then call comfy_install again to confirm it is loadable."
        )

    def status(self, request: ModelDownloadRequest) -> dict | None:
        return None

    def active(self, request: ModelDownloadRequest) -> bool:
        return False

    async def wait(self, requests: list[ModelDownloadRequest], abort, on_update=None) -> None:
        if requests:
            raise ValueError("no downloader on this ComfyUI; nothing was started")
