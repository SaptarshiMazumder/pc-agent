"""Choose an installation backend and confirm completion, independent of tool transport."""

import asyncio

from model_download_request import ModelDownloadRequest


class ModelInstallationService:
    def __init__(self, *, catalog, loadable, submit, start_manager, manager_busy,
                 queued_recently, mark_queued, wait_manager, await_loadable, lease, direct,
                 resolve_source=None):
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
        # A file entry -> the entry the GPU downloader can act on. The one job today: a Civitai
        # link becomes the signed storage URL (civitai_download_source.resolve). Identity when
        # the host wires nothing, so every Hugging Face path is byte-identical.
        self.resolve_source = resolve_source or (lambda file: file)

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
                request = ModelDownloadRequest(**self.resolve_source(file))
            except ValueError:
                if entry is None:
                    raise
                request = None  # Manager may support formats/hosts direct downloads forbid.
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
                elif entry is None:
                    report(f"{filename}: absent from Manager catalogue; using GPU-side downloader")
                    self.direct.start(request)
                    direct.append(request)
                elif self.queued_recently(filename) and self.manager_busy() is not False:
                    waiting.append(filename)
                    manager_files[filename] = file
                else:
                    try:
                        self.submit(file, entry)
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
            await self.direct.wait(fallback, abort, report)

        # Both channels are watched concurrently: a direct failure is not hidden behind
        # Manager's long queue, nor is a Manager outage hidden behind the direct download.
        tasks = [asyncio.create_task(wait_manager()),
                 asyncio.create_task(self.direct.wait(direct, abort, report))]
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
        return request
