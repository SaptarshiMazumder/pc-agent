"""Choose an installation backend and confirm completion, independent of tool transport."""

import asyncio

from model_download_request import ModelDownloadRequest


class ModelInstallationService:
    def __init__(self, *, catalog, loadable, submit, start_manager, manager_busy,
                 queued_recently, mark_queued, wait_manager, await_loadable, lease, direct):
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
            request = ModelDownloadRequest(**file) if entry is None else None
            plans.append((file, entry, request))
        queued, waiting, direct = [], [], []
        try:
            for file, entry, request in plans:
                if abort is not None and abort.is_set():
                    raise ValueError("Install cancelled before submission")
                self.lease()
                filename = file["filename"]
                if request is not None:
                    report(f"{filename}: absent from Manager catalogue; using GPU-side downloader")
                    self.direct.start(request)
                    direct.append(request)
                elif self.queued_recently(filename) and self.manager_busy():
                    waiting.append(filename)
                else:
                    self.submit(file, entry)
                    queued.append(filename)
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
            raise ValueError("Download finished but ComfyUI does not list: " + ", ".join(missing)
                             + ". Installation is NOT confirmed; check the model directory and refresh inventory.")
        if abort is not None and abort.is_set():
            raise ValueError("Install verification cancelled; success is unconfirmed")
        return {**present, **landed}
