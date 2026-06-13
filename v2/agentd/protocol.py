"""WebSocket wire protocol: request / response / event frames.

  {"type":"req",   "id":..., "method":..., "params":{...}}
  {"type":"res",   "id":..., "ok":true|false, "payload":{...}}
  {"type":"event", "event":..., "payload":{...}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Request:
    id: str
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    type: str = "req"


@dataclass
class Response:
    id: str
    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    type: str = "res"


@dataclass
class Event:
    event: str
    payload: dict[str, Any] = field(default_factory=dict)
    type: str = "event"


Frame = Request | Response | Event


class ProtocolError(Exception):
    pass


def parse_frame(raw: str) -> Frame:
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"invalid JSON frame: {e}") from e
    if not isinstance(d, dict):
        raise ProtocolError("frame must be a JSON object")
    t = d.get("type")
    if t == "req":
        if not d.get("id") or not d.get("method"):
            raise ProtocolError("req frame requires id and method")
        return Request(id=d["id"], method=d["method"], params=d.get("params") or {})
    if t == "res":
        return Response(id=d.get("id", ""), ok=bool(d.get("ok")), payload=d.get("payload") or {})
    if t == "event":
        return Event(event=d.get("event", ""), payload=d.get("payload") or {})
    raise ProtocolError(f"unknown frame type: {t!r}")


def dump_frame(frame: Frame) -> str:
    if isinstance(frame, Request):
        d = {"type": "req", "id": frame.id, "method": frame.method, "params": frame.params}
    elif isinstance(frame, Response):
        d = {"type": "res", "id": frame.id, "ok": frame.ok, "payload": frame.payload}
    elif isinstance(frame, Event):
        d = {"type": "event", "event": frame.event, "payload": frame.payload}
    else:
        raise ProtocolError(f"cannot serialize: {frame!r}")
    return json.dumps(d, ensure_ascii=False)
