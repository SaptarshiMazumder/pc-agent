"""ImageThumbnailService: fixed recipe, validators, and off-loop generation."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from agent_runtime.application.interfaces.image_thumbnail_generator import GeneratedImageThumbnail
from agent_runtime.application.services.image_thumbnail_service import (
    ImageThumbnailNotFoundError,
    ImageThumbnailService,
)


class RecordingImageThumbnailGenerator:
    def __init__(self):
        self.calls: list[tuple[Path, int, int]] = []

    def generate(self, source: Path, *, max_edge: int) -> GeneratedImageThumbnail:
        self.calls.append((source, max_edge, threading.get_ident()))
        return GeneratedImageThumbnail(b"small", "image/webp", 12, 8)


class ConcurrentImageThumbnailGenerator:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def generate(self, source: Path, *, max_edge: int) -> GeneratedImageThumbnail:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
        return GeneratedImageThumbnail(b"small", "image/webp", 12, 8)


class BlockingImageThumbnailGenerator:
    def __init__(self):
        self.active = 0
        self.calls = 0
        self.max_active = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()

    def generate(self, source: Path, *, max_edge: int) -> GeneratedImageThumbnail:
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.started.set()
        self.release.wait(timeout=2)
        with self.lock:
            self.active -= 1
        return GeneratedImageThumbnail(b"small", "image/webp", 12, 8)


def test_generation_is_fixed_size_and_runs_off_the_event_loop(tmp_path):
    source = tmp_path / "large.png"
    source.write_bytes(b"source")
    generator = RecordingImageThumbnailGenerator()
    service = ImageThumbnailService(generator)
    event_loop_thread = threading.get_ident()

    result = asyncio.run(service.get(source))

    assert result.data == b"small"
    assert result.mime_type == "image/webp"
    assert (result.width, result.height) == (12, 8)
    assert result.etag.startswith('W/"thumb-') and result.etag.endswith('"')
    assert generator.calls[0][0:2] == (source, 512)
    assert generator.calls[0][2] != event_loop_thread


def test_matching_etag_returns_empty_result_without_decoding_again(tmp_path):
    source = tmp_path / "large.png"
    source.write_bytes(b"source")
    generator = RecordingImageThumbnailGenerator()
    service = ImageThumbnailService(generator)

    first = asyncio.run(service.get(source))
    cached = asyncio.run(service.get(source, if_none_match=f'"other", {first.etag}'))

    assert cached.not_modified
    assert cached.data is None
    assert cached.etag == first.etag
    assert len(generator.calls) == 1


def test_source_revision_changes_the_etag(tmp_path):
    source = tmp_path / "large.png"
    source.write_bytes(b"one")
    generator = RecordingImageThumbnailGenerator()
    service = ImageThumbnailService(generator)

    first = asyncio.run(service.get(source))
    source.write_bytes(b"a different size")
    second = asyncio.run(service.get(source, if_none_match=first.etag))

    assert not second.not_modified
    assert second.etag != first.etag
    assert len(generator.calls) == 2


def test_concurrent_decodes_are_bounded(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    generator = ConcurrentImageThumbnailGenerator()
    service = ImageThumbnailService(generator, max_concurrent=1)

    async def generate_both():
        await asyncio.gather(service.get(first), service.get(second))

    asyncio.run(generate_both())

    assert generator.max_active == 1


def test_cancelled_request_holds_decode_slot_until_worker_finishes(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    generator = BlockingImageThumbnailGenerator()
    service = ImageThumbnailService(generator, max_concurrent=1)

    async def cancel_then_start_another():
        first_task = asyncio.create_task(service.get(first))
        assert await asyncio.to_thread(generator.started.wait, 1)
        first_task.cancel()
        await asyncio.sleep(0)

        second_task = asyncio.create_task(service.get(second))
        await asyncio.sleep(0.02)
        assert generator.calls == 1

        generator.release.set()
        with pytest.raises(asyncio.CancelledError):
            await first_task
        await second_task

    asyncio.run(cancel_then_start_another())

    assert generator.max_active == 1


def test_missing_source_is_not_found(tmp_path):
    service = ImageThumbnailService(RecordingImageThumbnailGenerator())

    with pytest.raises(ImageThumbnailNotFoundError):
        asyncio.run(service.get(tmp_path / "missing.png"))


@pytest.mark.parametrize("field", [{"max_edge": 0}, {"max_concurrent": 0}])
def test_invalid_limits_are_rejected(field):
    with pytest.raises(ValueError):
        ImageThumbnailService(RecordingImageThumbnailGenerator(), **field)
