"""Authorized, cache-aware image-thumbnail use case.

The service owns the preview rules: the size is fixed by composition, concurrent
decodes are bounded, and the HTTP validator changes whenever the source revision or
thumbnail recipe changes. Authorization happens before this use case at the gateway's
shared `/file` guard. Pixel decoding is delegated to the injected generator and moved
off the gateway's asyncio thread.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from agent_runtime.application.interfaces.image_thumbnail_generator import (
    GeneratedImageThumbnail,
    ImageThumbnailGenerator,
)


class ImageThumbnailNotFoundError(Exception):
    """The requested source is absent or outside the caller's authorized roots."""


@dataclass(frozen=True, slots=True)
class ImageThumbnailResult:
    etag: str
    data: bytes | None = None
    mime_type: str = "image/webp"
    width: int = 0
    height: int = 0

    @property
    def not_modified(self) -> bool:
        return self.data is None


class ImageThumbnailService:
    """Resolve one authorized source and render, or validate, its fixed-size preview."""

    _RECIPE_VERSION = "webp-v1"

    def __init__(
        self,
        generator: ImageThumbnailGenerator,
        *,
        max_edge: int = 512,
        max_concurrent: int = 2,
    ):
        if max_edge < 1:
            raise ValueError("max_edge must be positive")
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        self._generator = generator
        self._max_edge = max_edge
        self._decode_slots = asyncio.Semaphore(max_concurrent)

    async def get(
        self,
        source: Path,
        *,
        if_none_match: str = "",
    ) -> ImageThumbnailResult:
        try:
            stat = source.stat()
        except OSError as exc:
            raise ImageThumbnailNotFoundError(str(source)) from exc
        revision = (stat.st_size, stat.st_mtime_ns)
        etag = self._etag(source, revision)
        if self._etag_matches(if_none_match, etag):
            return ImageThumbnailResult(etag=etag)

        generated = await self._generate(source)

        # A producer can replace an artifact while its preview is being made. Re-read the
        # revision so a response never receives the old validator for new bytes. One retry is
        # enough: continuously rewritten outputs will simply be refreshed on the next request.
        try:
            after = source.stat()
        except OSError as exc:
            raise ImageThumbnailNotFoundError(str(source)) from exc
        after_revision = (after.st_size, after.st_mtime_ns)
        if after_revision != revision:
            generated = await self._generate(source)
            revision = after_revision
            etag = self._etag(source, revision)

        return ImageThumbnailResult(
            etag=etag,
            data=generated.data,
            mime_type=generated.mime_type,
            width=generated.width,
            height=generated.height,
        )

    async def _generate(self, source: Path) -> GeneratedImageThumbnail:
        async with self._decode_slots:
            operation = asyncio.create_task(
                asyncio.to_thread(
                    self._generator.generate,
                    source,
                    max_edge=self._max_edge,
                )
            )
            try:
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                # Cancelling to_thread() only cancels its asyncio wrapper; the decoder keeps
                # running. Keep this slot occupied until that worker really stops, otherwise a
                # client can repeatedly disconnect and exceed the configured decode limit.
                while not operation.done():
                    try:
                        await asyncio.shield(operation)
                    except asyncio.CancelledError:
                        continue
                with suppress(Exception):
                    operation.result()
                raise

    def _etag(self, source: Path, revision: tuple[int, int]) -> str:
        material = "\0".join(
            (
                self._RECIPE_VERSION,
                str(self._max_edge),
                os.path.normcase(str(source)),
                str(revision[0]),
                str(revision[1]),
            )
        ).encode("utf-8")
        # This validator is derived from the source's filesystem revision rather than by hashing
        # the emitted WebP bytes, so HTTP correctly calls it weak.
        return f'W/"thumb-{hashlib.sha256(material).hexdigest()}"'

    @staticmethod
    def _etag_matches(header: str, etag: str) -> bool:
        expected = etag[2:].strip() if etag.startswith("W/") else etag
        for candidate in (part.strip() for part in (header or "").split(",")):
            if candidate == "*":
                return True
            if candidate.startswith("W/"):
                candidate = candidate[2:].strip()
            if candidate == expected:
                return True
        return False
