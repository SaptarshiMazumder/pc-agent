"""EmailChannel — a Channel backed by the Gmail MCP you already run (Phase 5b/5a-ii).

REUSES the same Gmail MCP the agent uses: ``send`` calls the MCP's send tool, ``poll``
calls its search tool for new mail. No new email stack, no SMTP/IMAP. The same channel
serves agent replies AND email notifications (via ChannelNotifier).

The exact MCP tool NAMES and the search RESULT FORMAT depend on your workspace-mcp
build, so both are configurable (with sensible defaults) and the search parser is
defensive — confirm them against your live MCP and override in the channel config:

  {"type": "email", "agent": "support",
   "notify_to": "you@example.com",          # where notifications go (optional)
   "poll_query": "is:unread",
   "search_tool": "google__search_gmail_messages",
   "get_tool":    "google__get_gmail_message_content",
   "send_tool":   "google__send_gmail_message"}
"""

from __future__ import annotations

import json
import logging

from agent_runtime.domain.channels import InboundMessage

log = logging.getLogger(__name__)


def parse_search(raw: str) -> list[dict]:
    """Best-effort extraction of {id, from, body} from a search tool's text result.

    Handles a JSON array or a {messages|results|...: [...]} object. Returns [] for any
    shape it doesn't recognize (so an unmatched format never crashes the poll loop —
    it just finds nothing until the parser/keys are tuned to your MCP)."""
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 — plain-text result: nothing to parse
        return []
    items = (
        data
        if isinstance(data, list)
        else (data.get("messages") or data.get("results") or data.get("emails") or [])
    )
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        mid = str(it.get("id") or it.get("message_id") or it.get("messageId") or "").strip()
        if not mid:
            continue
        out.append(
            {
                "id": mid,
                "from": (it.get("from") or it.get("sender") or it.get("fromAddress") or "").strip(),
                "body": (it.get("body") or it.get("snippet") or it.get("text") or "").strip(),
            }
        )
    return out


class EmailChannel:
    name = "email"

    def __init__(self, invoke, agent_id: str = "main", cfg: dict | None = None):
        cfg = cfg or {}
        self.agent_id = agent_id
        self._invoke = invoke  # async (tool_name, params) -> str
        self._search_tool = cfg.get("search_tool", "google__search_gmail_messages")
        self._get_tool = cfg.get("get_tool", "google__get_gmail_message_content")
        self._send_tool = cfg.get("send_tool", "google__send_gmail_message")
        self._query = cfg.get("poll_query", "is:unread")
        self._subject = cfg.get("reply_subject", "Re: your message")
        self._seen: set[str] = set()  # message ids already processed (dedupe)

    async def send(self, peer: str, text: str) -> None:
        await self._invoke(self._send_tool, {"to": peer, "subject": self._subject, "body": text})

    async def poll(self) -> list[InboundMessage]:
        raw = await self._invoke(self._search_tool, {"query": self._query})
        out: list[InboundMessage] = []
        for m in parse_search(raw):
            if m["id"] in self._seen:
                continue
            self._seen.add(m["id"])
            body = m["body"]
            if not body and self._get_tool:  # fetch full content if search gave none
                try:
                    body = await self._invoke(self._get_tool, {"message_id": m["id"]})
                except Exception:  # noqa: BLE001
                    log.warning("email get_content failed for %s", m["id"], exc_info=True)
            out.append(
                InboundMessage(channel=self.name, peer=m["from"], text=body, external_id=m["id"])
            )
        return out
