"""Choose an installation backend and confirm completion, independent of tool transport."""

import asyncio

from model_download_request import ModelDownloadRequest
from gpu_model_download_failure import GpuModelDownloadFailure


class ModelInstallationService:
    def __init__(self, *, catalog, loadable, submit, start_manager, manager_busy,
                 queued_recently, mark_queued, wait_manager, await_loadable, lease, direct,
                 resolve_source=None, on_source=None):
        self.catalog = catalog
        self.loadable = loadable
        self.submit = submit
        self.start_manager = start_manager
        self.manager_busy = manager_busy
        self.queued_recently = queued_recently
        self.mark_queued = mark_queued
        self.wait_manager = wait_manager
        self.await_loadable = await_loadable
        self.lease = lease
        self.direct = direct
        # Provider authentication is handled by the injected resolver, before GPU I/O.
        self.resolve_source = resolve_source or (lambda file: file)
        self.on_source = on_source or (lambda file: None)

    async def install(self, files, abort, report):
        catalog = self.catalog()
        listed = self.loadable()
        present = {f["filename"]: listed[f["filename"].lower()]
                   for f in files if f["filename"].lower() in listed}
        plans = []
        for file in files:
            if file["filename"] in present:
                continue
            entry = next((m for m in catalog if str(m.get("filename", "")).lower()
                          == file["filename"].lower()), None)
            # Validate all uncatalogued requests BEFORE starting any expensive transfer.
            try:
                request = ModelDownloadRequest(**file)
            except ValueError:
                if entry is None:
                    raise
                request = None  # Manager may support formats/hosts direct downloads forbid.
            else:
                request = ModelDownloadRequest(**self.resolve_source(file))
            plans.append((file, entry, request))
        queued, waiting, direct = [], [], []
        manager_files = {}
        try:
            for file, entry, request in plans:
                if abort is not None and abort.is_set():
                    raise ValueError("Install cancelled before submission")
                self.lease()
                filename = file["filename"]
                if request is not None and entry is not None and self.direct.active(request):
                    report(f"{filename}: observing existing GPU download; not submitting to Manager")
                    direct.append(request)
                elif self.queued_recently(filename) and self.manager_busy() is not False:
                    waiting.append(filename)
                    manager_files[filename] = file
                elif entry is None or (request is not None and request.source in ("civitai", "huggingface")):
                    reason = "absent from Manager catalogue" if entry is None else "provider download link resolved"
                    report(f"{filename}: {reason}; using GPU-side downloader")
                    self.direct.start(request)
                    self.on_source({"filename": request.filename, "url": request.origin_url or request.url,
                                    "kind": request.kind})
                    direct.append(request)
                else:
                    try:
                        self.submit(file, entry)
                        self.on_source({**file, "url": entry.get("url") or file["url"]})
                    except Exception as error:
                        # A timeout may have accepted the request. Never race an unknown
                        # or active Manager writer with a second download backend.
                        if self.manager_busy() is not False:
                            raise ValueError(f"{filename}: Manager submission unconfirmed ({error}); "
                                             "downloads may continue; no duplicate started") from error
                        direct.append(self._start_fallback(file, str(error), abort, report))
                        continue
                    queued.append(filename)
                    manager_files[filename] = file
                    self.mark_queued([filename])
                    report(f"{filename}: accepted by Manager; not installed yet")
        except Exception as error:
            report(f"Install failed: {error}")
            # Previously accepted jobs must not be stranded in a stopped Manager queue.
            if queued:
                self.start_manager()
            raise ValueError(f"{error}. Previously accepted GPU/Manager downloads may continue.") from error
        if queued:
            self.start_manager()

        async def wait_manager():
            if not (queued or waiting):
                return
            state = await self.wait_manager(abort, report)
            if state != "idle":
                raise ValueError(f"Manager wait ended as {state}; installation unconfirmed, downloads may continue")
            self._check_abort(abort)
            landed = await self.await_loadable(list(manager_files), abort)
            self._check_abort(abort)
            fallback = []
            for filename, file in manager_files.items():
                if filename in landed:
                    continue
                # Another chat can have started Manager during the inventory grace.
                if self.manager_busy() is not False:
                    raise ValueError(f"{filename}: Manager activity is not confirmed idle; "
                                     "installation unconfirmed, no duplicate started")
                fallback.append(self._start_fallback(
                    file, "Manager is idle but ComfyUI does not list the file", abort, report,
                ))
            await self._wait_direct(fallback, abort, report)

        # Both channels are watched concurrently: a direct failure is not hidden behind
        # Manager's long queue, nor is a Manager outage hidden behind the direct download.
        tasks = [asyncio.create_task(wait_manager()),
                 asyncio.create_task(self._wait_direct(direct, abort, report))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        pending = queued + waiting + [r.filename for r in direct]
        landed = await self.await_loadable(pending, abort) if pending else {}
        missing = [f for f in pending if f not in landed]
        if missing:
            raise ValueError("ComfyUI does not list: " + ", ".join(missing)
                             + ". Installation is NOT confirmed; do not claim the files downloaded or run yet.")
        if abort is not None and abort.is_set():
            raise ValueError("Install verification cancelled; success is unconfirmed")
        return {**present, **landed}

    @staticmethod
    def _check_abort(abort):
        if abort is not None and abort.is_set():
            raise ValueError("Install cancelled; no further downloads started")

    def _start_fallback(self, file, reason, abort, report):
        self._check_abort(abort)
        try:
            # Resolved again, not reused: a Civitai storage URL is signed for a while, and this
            # fallback can come minutes after the first resolution.
            request = ModelDownloadRequest(**self.resolve_source(file))
        except ValueError as error:
            raise ValueError(f"{file['filename']}: {reason}. GPU fallback unavailable: {error}") from error
        self.lease()
        report(f"{request.filename}: {reason}; retrying with GPU-side downloader")
        self.direct.start(request)
        self.on_source({"filename": request.filename, "url": request.origin_url or request.url,
                        "kind": request.kind})
        return request

    async def _wait_direct(self, requests, abort, report):
        pending = list(requests)
        refreshed = set()
        while True:
            try:
                await self.direct.wait(pending, abort, report)
                return
            except GpuModelDownloadFailure as error:
                request = error.request
                if (error.http_status not in (401, 403)
                        or request.source not in ("civitai", "huggingface")
                        or not request.origin_url or request.job_id in refreshed):
                    raise
                self._check_abort(abort)
                refreshed.add(request.job_id)
                report(f"{request.filename}: storage refused the download; refreshing the provider link once")
                fresh = ModelDownloadRequest(**self.resolve_source({
                    "filename": request.filename, "kind": request.kind, "url": request.origin_url,
                }))
                self.lease()
                self.direct.start(fresh)
                pending = [fresh if item.job_id == request.job_id else item for item in pending]
