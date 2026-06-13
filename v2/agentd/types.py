"""Message and content-block data model.

Mirrors the reference agent's llm-core message types:
  UserMessage | AssistantMessage | ToolResultMessage
with content blocks Text | Thinking | Image | ToolCall.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Union

STOP_REASONS = ("stop", "length", "toolUse", "error", "aborted")


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Content blocks
# ---------------------------------------------------------------------------


@dataclass
class TextContent:
    text: str
    type: str = "text"


@dataclass
class ThinkingContent:
    thinking: str
    type: str = "thinking"


@dataclass
class ImageContent:
    data: str  # base64
    mime_type: str
    type: str = "image"


@dataclass
class ToolCallContent:
    id: str
    name: str
    arguments: dict[str, Any]
    type: str = "toolCall"


ContentBlock = Union[TextContent, ThinkingContent, ImageContent, ToolCallContent]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@dataclass
class UserMessage:
    content: str
    timestamp: int = field(default_factory=now_ms)
    role: str = "user"


@dataclass
class AssistantMessage:
    content: list[ContentBlock] = field(default_factory=list)
    stop_reason: str = "stop"  # stop | length | toolUse | error | aborted
    usage: dict[str, int] = field(default_factory=dict)  # {"input": n, "output": n}
    model: str | None = None
    error_message: str | None = None
    timestamp: int = field(default_factory=now_ms)
    role: str = "assistant"

    @property
    def tool_calls(self) -> list[ToolCallContent]:
        return [b for b in self.content if isinstance(b, ToolCallContent)]

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextContent))


@dataclass
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    content: list[ContentBlock] = field(default_factory=list)
    is_error: bool = False
    timestamp: int = field(default_factory=now_ms)
    role: str = "toolResult"

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextContent))


Message = Union[UserMessage, AssistantMessage, ToolResultMessage]


# ---------------------------------------------------------------------------
# Serialization (JSONL transcript + wire format)
# ---------------------------------------------------------------------------


def content_to_dict(block: ContentBlock) -> dict[str, Any]:
    if isinstance(block, TextContent):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingContent):
        return {"type": "thinking", "thinking": block.thinking}
    if isinstance(block, ImageContent):
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
    if isinstance(m, UserMessage):
        return {"role": "user", "content": m.content, "timestamp": m.timestamp}
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
        return {
            "role": "toolResult",
            "toolCallId": m.tool_call_id,
            "toolName": m.tool_name,
            "content": [content_to_dict(b) for b in m.content],
            "isError": m.is_error,
            "timestamp": m.timestamp,
        }
    raise TypeError(f"unknown message: {m!r}")


def message_from_dict(d: dict[str, Any]) -> Message:
    role = d.get("role")
    if role == "user":
        return UserMessage(content=d.get("content", ""), timestamp=d.get("timestamp", 0))
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
            timestamp=d.get("timestamp", 0),
        )
    raise ValueError(f"unknown message role: {role!r}")
