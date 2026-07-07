"""Message & content-block data model — the conversation's vocabulary.

This is the DOMAIN layer: pure data, no IO, no external libraries. Every other
layer (the LLM service, the agent loop, memory, the gateway) passes these objects
around, so they are the shared "language" of the whole agent.

A conversation is just an ordered ``list[Message]``. That list is:
  * what gets RE-SENT to the LLM on every loop iteration (the model is stateless —
    it remembers nothing between calls, so the agent re-feeds the whole history), and
  * what gets PERSISTED to the JSONL session transcript (via ``message_to_dict``).

Two shapes exist for the same data:
  * the Python objects below (used in code), and
  * plain dicts (used on disk / on the wire) — converted by the ``*_to_dict`` /
    ``*_from_dict`` helpers at the bottom. The dict form uses camelCase keys
    (``stopReason``, ``toolCallId``) to match the JSON wire/transcript format.

Mirrors the reference agent's llm-core message types:
  UserMessage | AssistantMessage | ToolResultMessage
with content blocks Text | Thinking | Image | ToolCall.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Union

# Allowed values for AssistantMessage.stop_reason — why the model stopped this turn:
#   stop    = finished normally (final answer)
#   length  = hit the max-token limit
#   toolUse = stopped to call tool(s) -> the loop will run them and continue
#   error   = the LLM call failed
#   aborted = the user cancelled mid-run
STOP_REASONS = ("stop", "length", "toolUse", "error", "aborted")


def now_ms() -> int:
    """Current wall-clock time in milliseconds (used as the default message timestamp)."""
    return int(time.time() * 1000)


# ===========================================================================
# Content blocks — the small pieces a message's content is built from.
# A single assistant turn can mix several (e.g. some thinking + text + a tool call).
# Each block carries a literal ``type`` tag so it can be serialized/deserialized.
# ===========================================================================


@dataclass
class TextContent:
    """Plain visible text (what the user actually reads)."""
    text: str
    type: str = "text"  # discriminator tag (kept in the serialized form)


@dataclass
class ThinkingContent:
    """The model's internal reasoning (shown only if reasoning display is on)."""
    thinking: str
    type: str = "thinking"


@dataclass
class ImageContent:
    """An image, carried inline as base64 (e.g. a screenshot the agent produced)."""
    data: str  # base64-encoded image bytes
    mime_type: str  # e.g. "image/png"
    type: str = "image"


@dataclass
class ToolCallContent:
    """A request from the model to run a tool. Produced by the LLM, executed by the loop."""
    id: str  # unique id for this call; the matching ToolResultMessage references it
    name: str  # which tool to run, e.g. "read" / "exec" / "browser"
    arguments: dict[str, Any]  # the tool's parameters, e.g. {"path": "cv.docx"}
    type: str = "toolCall"


# Any one of the block kinds above. A message's ``content`` is a list of these.
ContentBlock = Union[TextContent, ThinkingContent, ImageContent, ToolCallContent]


@dataclass
class Artifact:
    """A file a tool DECLARED it produced — a deliverable to present to the user (an
    image, video, deck, …). This is a PRESENTATION channel, kept separate from the
    model-facing ``content``: it is never inferred from text, only what a producing tool
    explicitly hands back (see ToolResult.artifacts). Carried by reference (path); the
    client streams the bytes from the daemon's /file endpoint, so large media never
    bloats the transcript."""
    path: str
    name: str = ""
    mime: str = ""
    kind: str = "file"  # image | video | audio | file (how a client renders it)
    size: int = 0


def artifact_to_dict(a: "Artifact") -> dict[str, Any]:
    return {"path": a.path, "name": a.name, "mime": a.mime, "kind": a.kind, "size": a.size}


def artifact_from_dict(d: dict[str, Any]) -> "Artifact":
    return Artifact(
        path=d.get("path", ""), name=d.get("name", ""), mime=d.get("mime", ""),
        kind=d.get("kind", "file"), size=int(d.get("size") or 0),
    )


# ===========================================================================
# Messages — the three "roles" that make up a conversation.
# ===========================================================================


@dataclass
class UserMessage:
    """Something the user said. Its visible text is a plain string; it may also carry
    ``attachments`` — files the user handed in (an edited image from the canvas, a picked
    document). Attachments are carried BY REFERENCE (Artifact path), exactly like a tool's
    declared deliverables: the transcript stays lean, the client streams the bytes from
    /file, and the LLM adapter inlines any IMAGE attachment so a vision model can SEE it."""
    content: str
    attachments: list[Artifact] = field(default_factory=list)  # user-supplied files (by ref)
    timestamp: int = field(default_factory=now_ms)  # when it was created (ms)
    role: str = "user"  # discriminator tag


@dataclass
class AssistantMessage:
    """One turn produced by the model. May contain thinking, text, and/or tool calls."""
    content: list[ContentBlock] = field(default_factory=list)  # the blocks of this turn
    stop_reason: str = "stop"  # one of STOP_REASONS — why the turn ended
    usage: dict[str, int] = field(default_factory=dict)  # token counts: {"input": n, "output": n}
    model: str | None = None  # which model produced it (e.g. "gemini/gemini-2.5-pro")
    error_message: str | None = None  # set only when stop_reason == "error"
    timestamp: int = field(default_factory=now_ms)
    role: str = "assistant"

    @property
    def tool_calls(self) -> list[ToolCallContent]:
        """The tool-call blocks in this turn (empty if the model just answered).
        The loop reads this to decide whether to run tools and loop again, or finish."""
        return [b for b in self.content if isinstance(b, ToolCallContent)]

    @property
    def text(self) -> str:
        """All visible text of this turn, concatenated (ignores thinking/tool blocks)."""
        return "".join(b.text for b in self.content if isinstance(b, TextContent))


@dataclass
class ToolResultMessage:
    """The output of one executed tool, fed back to the model on the next iteration."""
    tool_call_id: str  # ties this result to the ToolCallContent.id that requested it
    tool_name: str  # which tool produced it
    content: list[ContentBlock] = field(default_factory=list)  # usually a single TextContent
    is_error: bool = False  # True if the tool failed (the model sees it as an error)
    artifacts: list[Artifact] = field(default_factory=list)  # files the tool DECLARED it produced
    timestamp: int = field(default_factory=now_ms)
    role: str = "toolResult"

    @property
    def text(self) -> str:
        """The tool's output as text."""
        return "".join(b.text for b in self.content if isinstance(b, TextContent))


# Any one of the three message kinds. A conversation is an ordered ``list[Message]``.
Message = Union[UserMessage, AssistantMessage, ToolResultMessage]


# ===========================================================================
# Serialization — convert between the Python objects above and plain dicts.
# Used for the JSONL transcript on disk and for sending messages over the WebSocket.
# Object  --(*_to_dict)-->  dict (camelCase keys)  --(*_from_dict)-->  Object
# ===========================================================================


def content_to_dict(block: ContentBlock) -> dict[str, Any]:
    """Turn one content block into a JSON-safe dict (dispatch on its concrete type)."""
    if isinstance(block, TextContent):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingContent):
        return {"type": "thinking", "thinking": block.thinking}
    if isinstance(block, ImageContent):
        # note: snake_case ``mime_type`` becomes camelCase ``mimeType`` in the dict form
        return {"type": "image", "data": block.data, "mimeType": block.mime_type}
    if isinstance(block, ToolCallContent):
        return {
            "type": "toolCall",
            "id": block.id,
            "name": block.name,
            "arguments": block.arguments,
        }
    raise TypeError(f"unknown content block: {block!r}")


def content_from_dict(d: dict[str, Any]) -> ContentBlock:
    """Rebuild a content block from its dict form (inverse of ``content_to_dict``).
    Uses ``.get(...)`` with defaults so a slightly malformed/old record won't crash."""
    t = d.get("type")
    if t == "text":
        return TextContent(text=d.get("text", ""))
    if t == "thinking":
        return ThinkingContent(thinking=d.get("thinking", ""))
    if t == "image":
        return ImageContent(data=d.get("data", ""), mime_type=d.get("mimeType", ""))
    if t == "toolCall":
        return ToolCallContent(
            id=d.get("id", ""),
            name=d.get("name", ""),
            arguments=d.get("arguments") or {},
        )
    raise ValueError(f"unknown content block type: {t!r}")


def message_to_dict(m: Message) -> dict[str, Any]:
    """Turn a whole message into a JSON-safe dict (for the transcript / the wire).
    Content blocks are serialized via ``content_to_dict``; keys are camelCase."""
    if isinstance(m, UserMessage):
        d = {"role": "user", "content": m.content, "timestamp": m.timestamp}
        if m.attachments:  # only when the user attached something (old records unchanged)
            d["attachments"] = [artifact_to_dict(a) for a in m.attachments]
        return d
    if isinstance(m, AssistantMessage):
        return {
            "role": "assistant",
            "content": [content_to_dict(b) for b in m.content],
            "stopReason": m.stop_reason,
            "usage": m.usage,
            "model": m.model,
            "errorMessage": m.error_message,
            "timestamp": m.timestamp,
        }
    if isinstance(m, ToolResultMessage):
        d = {
            "role": "toolResult",
            "toolCallId": m.tool_call_id,
            "toolName": m.tool_name,
            "content": [content_to_dict(b) for b in m.content],
            "isError": m.is_error,
            "timestamp": m.timestamp,
        }
        if m.artifacts:  # only when the tool declared something (keeps old records identical)
            d["artifacts"] = [artifact_to_dict(a) for a in m.artifacts]
        return d
    raise TypeError(f"unknown message: {m!r}")


def message_from_dict(d: dict[str, Any]) -> Message:
    """Rebuild a message from its dict form (inverse of ``message_to_dict``).
    This is how a session is replayed from the JSONL transcript on startup."""
    role = d.get("role")
    if role == "user":
        return UserMessage(
            content=d.get("content", ""),
            attachments=[artifact_from_dict(a) for a in d.get("attachments") or []],
            timestamp=d.get("timestamp", 0),
        )
    if role == "assistant":
        return AssistantMessage(
            content=[content_from_dict(b) for b in d.get("content") or []],
            stop_reason=d.get("stopReason", "stop"),
            usage=d.get("usage") or {},
            model=d.get("model"),
            error_message=d.get("errorMessage"),
            timestamp=d.get("timestamp", 0),
        )
    if role == "toolResult":
        return ToolResultMessage(
            tool_call_id=d.get("toolCallId", ""),
            tool_name=d.get("toolName", ""),
            content=[content_from_dict(b) for b in d.get("content") or []],
            is_error=d.get("isError", False),
            artifacts=[artifact_from_dict(a) for a in d.get("artifacts") or []],
            timestamp=d.get("timestamp", 0),
        )
    raise ValueError(f"unknown message role: {role!r}")
