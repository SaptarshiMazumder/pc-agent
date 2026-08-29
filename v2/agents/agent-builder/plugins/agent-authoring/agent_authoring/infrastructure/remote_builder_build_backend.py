"""RemoteBuilderBuildBackend — the build runs in the builder service, not in this process.

THE HOSTED BACKEND. A node build's memory peak exceeds the whole daemon task on a hosted
deployment, so the build goes out: zip ``app/``, hand it to the builder Lambda
(services/builder/handler.py), bring the built ``ui/`` home. The agent's files never stop
living here — the scratch bucket in the middle is a conveyor belt that expires daily.

PURE HTTP, NO AWS SDK. The daemon image carries no boto3, and one build is not a reason to add
it: the builder presigns every S3 URL this side touches (``op: presign`` for the upload, a
presigned GET in the build response), so the whole exchange is four requests any HTTP client
can make. The internal key rides each builder call; the presigned URLs carry their own auth.

SYNCHRONOUS ON PURPOSE — same as the local backend: BuildAppService runs inside
``asyncio.to_thread`` (the event-loop-freeze fix), so blocking here is correct and an async
client would just be a second way to do the same thing.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import httpx

from agent_authoring.application.build_backend import BuildBackendError, BuildBackendOutcome

#: Never shipped to the builder: build output and installed packages are inputs to nothing.
_SKIP_DIRS = frozenset({"node_modules", ".vite", "dist"})

#: One build, end to end. The builder's own ceiling is 600s (builder_timeout_seconds); the
#: margin covers the two S3 transfers.
_REQUEST_TIMEOUT_S = 660.0


class RemoteBuilderBuildBackend:
    """:param builder_url: the builder service base URL (ALB :4400).
    :param internal_key: shared secret presented as X-Internal-Key on builder calls."""

    def __init__(self, builder_url: str, internal_key: str = ""):
        self._base = builder_url.rstrip("/")
        self._key = internal_key

    def build(self, app_dir: Path) -> BuildBackendOutcome:
        agent_id = app_dir.parent.name or "agent"
        sources = self._zip_sources(app_dir)

        # 1. somewhere to put the sources (the builder signs the URL; we just PUT)
        grant = self._ask({"op": "presign", "agent_id": agent_id})
        put_url = str(grant.get("put_url") or "")
        sources_key = str(grant.get("sources_key") or "")
        if not put_url or not sources_key:
            raise BuildBackendError(
                f"the builder's presign answer is missing its URL or key: {grant}"
            )

        # 2. up
        try:
            r = httpx.put(put_url, content=sources, timeout=_REQUEST_TIMEOUT_S)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise BuildBackendError(f"uploading the sources to the build scratch failed: {e}") from e

        # 3. build
        answer = self._ask({"sources_key": sources_key, "agent_id": agent_id})
        log_tail = str(answer.get("log_tail") or "")
        if not answer.get("ok"):
            # The tail carries vite's own error — file and line — which is the whole point of
            # shipping the log back instead of a verdict.
            raise BuildBackendError(f"the build failed:\n\n{log_tail or answer.get('error') or answer}")
        result_url = str(answer.get("result_url") or "")
        if not result_url:
            raise BuildBackendError(f"the builder answered ok but sent no result_url: {answer}")

        # 4. down, and into ui/ — REPLACING it. A stale asset surviving beside fresh ones is how
        # a window half-updates; vite's own emptyOutDir does the same locally.
        try:
            r = httpx.get(result_url, timeout=_REQUEST_TIMEOUT_S)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise BuildBackendError(f"downloading the built ui/ failed: {e}") from e
        self._unpack_ui(r.content, app_dir.parent / "ui")

        return BuildBackendOutcome(dependencies="remote", output=log_tail)

    # ------------------------------------------------------------------ plumbing

    def _ask(self, payload: dict) -> dict:
        headers = {"X-Internal-Key": self._key} if self._key else {}
        try:
            r = httpx.post(
                f"{self._base}/build", json=payload, headers=headers, timeout=_REQUEST_TIMEOUT_S
            )
        except httpx.HTTPError as e:
            raise BuildBackendError(
                f"the builder service is unreachable at {self._base}: {e}"
            ) from e
        try:
            answer = r.json()
        except ValueError:
            raise BuildBackendError(
                f"the builder answered HTTP {r.status_code} with a non-JSON body: {r.text[:300]}"
            ) from None
        if r.status_code >= 400 and not isinstance(answer, dict):
            raise BuildBackendError(f"the builder refused (HTTP {r.status_code}): {r.text[:300]}")
        return answer if isinstance(answer, dict) else {}

    @staticmethod
    def _zip_sources(app_dir: Path) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(app_dir.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(app_dir)
                if any(part in _SKIP_DIRS for part in rel.parts):
                    continue
                z.write(f, rel.as_posix())
        return buf.getvalue()

    @staticmethod
    def _unpack_ui(zip_bytes: bytes, ui_dir: Path) -> None:
        import shutil

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            # Traversal guard: the result is OUR builder's output, but a guard costs three lines
            # and its absence is a policy that zip contents are trusted — which must never be
            # true of anything that crossed the network.
            root = ui_dir.resolve()
            members = z.infolist()
            for m in members:
                target = (ui_dir / m.filename).resolve()
                if not str(target).startswith(str(root) + os.sep) and target != root:
                    raise BuildBackendError(f"result zip member escapes ui/: {m.filename}")
            if ui_dir.exists():
                shutil.rmtree(ui_dir)
            ui_dir.mkdir(parents=True)
            z.extractall(ui_dir)
