"""Background embedding — make ``remember`` return instantly.

A note's fact is written NOW (fast local sqlite insert, ``embedding=NULL``); its vector is
computed off the turn so the agent never waits on the network. The blocking litellm call runs in
a worker thread (``asyncio.to_thread``); the DB ``UPDATE`` happens back on the loop thread — the
same thread that owns the sqlite connection — so nothing crosses threads at the DB.

Failures are safe: the note stays keyword-searchable with a NULL vector. Re-embedding those rows
(after an outage or an embed-model change) is a deferred backfill — see the project memory note.
In-flight tasks are kept referenced (so they aren't GC'd mid-flight) and discarded on completion.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("agentd")


class BackgroundEmbedder:
    """Fire-and-forget embed→store, bound to one bank. No worker/loop lifecycle to manage: it
    schedules a task per note on the running loop and cleans up after itself."""

    def __init__(self, bank):
        self._bank = bank
        self._tasks: set = set()

    def schedule(self, item_id: str, text: str) -> None:
        """Queue a background embed for an already-saved (NULL-vector) note. No-op when there's no
        embedder or no running loop — the note simply stays keyword-only until a later backfill."""
        if not getattr(self._bank, "embedder_ready", False):
            return
        try:
            task = asyncio.create_task(self._run(item_id, text))
        except RuntimeError:                    # no running loop (e.g. a sync/CLI path)
            log.warning("memory: no running loop; note %s kept keyword-only", item_id)
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, item_id: str, text: str) -> None:
        try:
            vec = await asyncio.to_thread(self._bank.embed_vector, text)   # network, off the loop
        except Exception as e:  # noqa: BLE001 — embed is best-effort; the note is already saved
            log.warning("memory: background embed failed (note kept, keyword-only): %s", e)
            return
        try:
            self._bank.store_embedding(item_id, vec)     # DB write, back on the loop thread
            log.debug("memory: embedded note %s in background", item_id)
        except Exception as e:  # noqa: BLE001
            log.warning("memory: store embedding failed for %s: %s", item_id, e)

    async def drain(self) -> None:
        """Await all in-flight embeds — for tests and graceful shutdown. Safe when none pending."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
