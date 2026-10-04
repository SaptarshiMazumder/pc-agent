"""ComfyCloudModelImporter — a model file Comfy Cloud does not have, imported into the person's account.

Comfy Cloud has 1,300+ models preinstalled; anything else is imported from Hugging Face or Civitai
with `POST /api/assets/download` (Creator plan and above), which downloads it on Comfy's side —
no GPU of ours, no bytes through the daemon. A 200 means it was already in storage; a 202 is a
background task, followed at `GET /api/tasks/{task_id}`. A file counts as installed only when
ComfyUI lists it among a loader's choices (`await_loadable`), as everywhere else.

EVERY FILE IS ANSWERED. A file from another host, an unknown folder or a refused import (a plan
without imports answers 403) is named with the reason; the others still go ahead.

A CIVITAI FILE THAT NEEDS A LOGIN (most LoRAs) is imported with the person's own Civitai key, in the
link Comfy Cloud fetches (`?token=${USER_CIVITAI_TOKEN}`, a name the host substitutes). Bare first,
the key only on a failed import: an unset key would arrive as the literal `${…}` and spoil even a
public download.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from model_download_request import ModelDownloadRequest

#: The only hosts Comfy Cloud imports from (its API refuses the rest).
IMPORT_HOSTS = ("huggingface.co", "civitai.com")
#: The person's Civitai key, as the name the host substitutes.
CIVITAI_KEY_REF = "${USER_CIVITAI_TOKEN}"


class ComfyCloudModelImporter:
    def __init__(self, post: Callable, get: Callable,
                 await_loadable: Callable[[list[str], object], Awaitable[dict[str, str]]],
                 wait_s: float, poll_s: float = 5.0) -> None:
        self._post = post
        self._get = get
        self._await_loadable = await_loadable
        self._wait_s = wait_s
        self._poll_s = poll_s

    async def install(self, files: list[dict], abort, report) -> dict[str, str]:
        """{filename: loader name} for every file now loadable. ValueError naming each one that
        is not — refused, still importing, or imported but not listed."""
        refused: list[str] = []
        tasks: dict[str, str] = {}  # filename -> task id
        ready: list[str] = []
        sources: dict[str, tuple[str, str]] = {}  # filename -> (url, folder), for the keyed retry
        for f in files:
            name, url = f["filename"], str(f.get("url") or "")
            why = self._unimportable(url, str(f.get("kind") or ""))
            if why:
                refused.append(f"{name} ({why})")
                continue
            sources[name] = (url, ModelDownloadRequest.folder_of(str(f["kind"])))
            self._start(name, *sources[name], ready, tasks, refused, report)
        pending = await self._follow(tasks, refused, abort, report, sources, ready)
        ready += [n for n in tasks if n not in pending]
        landed = await self._await_loadable(ready, abort) if ready else {}
        missing = [n for n in ready if n not in landed]
        if refused or pending or missing:
            parts = []
            if refused:
                parts.append("NOT installed: " + "; ".join(refused))
            if pending:
                parts.append("still importing on Comfy Cloud (call comfy_install again to wait for them): "
                             + ", ".join(pending))
            if missing:
                parts.append("imported but not listed by ComfyUI yet: " + ", ".join(missing))
            if landed:
                parts.append("Installed: " + ", ".join(landed))
            raise ValueError(". ".join(parts) + ".")
        return landed

    def _start(self, name: str, url: str, folder: str, ready: list[str], tasks: dict[str, str],
               refused: list[str], report) -> None:
        """Ask Comfy Cloud to import one file: ready (200), a task (202), or refused — a refused bare
        Civitai link is asked once more with the person's key."""
        res = self._post("/api/assets/download", {
            "source_url": url, "tags": ["models", folder], "user_metadata": {"filename": name}})
        if res.status == 200:
            ready.append(name)
        elif res.status == 202:
            tasks[name] = str((res.json() or {}).get("task_id") or "")
            report(f"{name}: Comfy Cloud is importing it")
        elif self._keyed(url) != url:
            self._start(name, self._keyed(url), folder, ready, tasks, refused, report)
        else:
            refused.append(f"{name} (Comfy Cloud refused the import: HTTP {res.status} "
                           f"{(res.text or '')[:200]})")

    async def _follow(self, tasks: dict[str, str], refused: list[str], abort, report,
                      sources: dict[str, tuple[str, str]], ready: list[str]) -> list[str]:
        """Wait for the import tasks; returns the names still running when the wait ends. A Civitai
        import that failed without the person's key is started once more with it."""
        deadline = time.monotonic() + self._wait_s
        running = dict(tasks)
        while running and time.monotonic() < deadline:
            if abort is not None and abort.is_set():
                break
            for name, task in list(running.items()):
                res = self._get(f"/api/tasks/{task}")
                status = str((res.json() or {}).get("status") or "") if res.ok else ""
                if status == "completed":
                    running.pop(name)
                    report(f"{name}: imported")
                elif status == "failed" or (not res.ok and res.status not in (0, 429, 500, 502, 503, 504)):
                    running.pop(name)
                    tasks.pop(name)
                    url, folder = sources[name]
                    if self._keyed(url) != url:
                        report(f"{name}: Civitai wants a login — importing it with your Civitai key")
                        sources[name] = (self._keyed(url), folder)
                        self._start(name, *sources[name], ready, tasks, refused, report)
                        if name in tasks:
                            running[name] = tasks[name]
                        continue
                    detail = (res.json() or {}).get("message") if res.ok else f"HTTP {res.status}"
                    hint = "; the Civitai key in Settings may be missing or not allowed this file" if "civitai.com" in url else ""
                    refused.append(f"{name} (the import failed on Comfy Cloud: {detail}{hint})")
            if running:
                await asyncio.sleep(self._poll_s)
        return list(running)

    @staticmethod
    def _keyed(url: str) -> str:
        """A civitai.com link carrying the person's key; any other link, or one already keyed, as is."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        query = parse_qsl(parts.query, keep_blank_values=True)
        if not (host == "civitai.com" or host.endswith(".civitai.com")) or any(k == "token" for k, _ in query):
            return url
        keyed = "&".join(q for q in (urlencode(query), "token=" + CIVITAI_KEY_REF) if q)
        return urlunsplit(parts._replace(query=keyed))

    @staticmethod
    def _unimportable(url: str, kind: str) -> str:
        host = (urlsplit(url).hostname or "").lower()
        if not url.startswith("https://") or not any(host == h or host.endswith("." + h) for h in IMPORT_HOSTS):
            return "Comfy Cloud imports only from huggingface.co or civitai.com links"
        if not ModelDownloadRequest.folder_of(kind):
            return f"'{kind}' is not a ComfyUI model folder"
        return ""


__all__ = ["CIVITAI_KEY_REF", "ComfyCloudModelImporter"]
