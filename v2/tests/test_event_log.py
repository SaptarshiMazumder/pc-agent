"""Durable event log: every run event is appended to <events>/<agent>-<run>.jsonl, the handle
closes on agent_end, the dir is pruned to the most recent N runs, and the gateway feeds it."""

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.domain.events import AgentEvent
from agentd.infrastructure.events import FileEventLog, build_event_log


def _records(path: Path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_emit_writes_one_file_per_run_named_by_agent(tmp_path):
    log = FileEventLog(tmp_path)
    sk = "agent:expense-calc:cron:abc"
    log.emit(sk, "run1", AgentEvent("agent_start", {}))
    log.emit(sk, "run1", AgentEvent("tool_execution_start", {"toolName": "exec", "args": {}}))
    log.emit(sk, "run1", AgentEvent("agent_end", {"stopReason": "stop"}))
    f = tmp_path / "expense-calc-run1.jsonl"        # named by agent (from session key) + run id
    assert f.is_file()
    recs = _records(f)
    assert [r["event"]["type"] for r in recs] == ["agent_start", "tool_execution_start", "agent_end"]
    assert recs[0]["sessionKey"] == sk and recs[0]["runId"] == "run1"
    assert recs[1]["event"]["toolName"] == "exec"


def test_agent_end_releases_the_handle(tmp_path):
    log = FileEventLog(tmp_path)
    log.emit("agent:main:dev", "r", AgentEvent("agent_start", {}))
    assert "r" in log._handles
    log.emit("agent:main:dev", "r", AgentEvent("agent_end", {"stopReason": "stop"}))
    assert "r" not in log._handles                  # closed + dropped on agent_end
    log.close()


def test_prune_drops_oldest_runs(tmp_path):
    log = FileEventLog(tmp_path, max_runs=2)
    for i in range(4):
        log.emit("agent:a:dev", f"run{i}", AgentEvent("agent_start", {}))
        log.emit("agent:a:dev", f"run{i}", AgentEvent("agent_end", {"stopReason": "stop"}))
        os.utime(tmp_path / f"a-run{i}.jsonl", (1000 + i, 1000 + i))   # deterministic mtimes
    names = {p.name for p in tmp_path.glob("*.jsonl")}
    assert "a-run0.jsonl" not in names              # oldest pruned
    assert "a-run3.jsonl" in names                  # newest kept
    assert len(names) <= 3                          # ~max_runs (+1 transient before next prune)
    log.close()


def test_build_event_log_gated_by_flag(tmp_path):
    assert build_event_log(SimpleNamespace(event_log_enabled=False)) is None
    el = build_event_log(SimpleNamespace(
        event_log_enabled=True, state_dir=tmp_path, event_log_max_runs=50))
    assert el is not None
    el.close()


@pytest.mark.asyncio
async def test_gateway_feeds_the_event_log(tmp_path):
    # the gateway's on_event sink forwards every event to the injected event log
    from agentd.presentation.gateway import Gateway, RunHandle

    recorded = []

    class FakeLog:
        def emit(self, sk, rid, ev):
            recorded.append((sk, rid, ev.type))

        def close(self):
            pass

    class FakeService:
        async def handle_message(self, sk, message, on_event, abort, mode=None, agent_id=None,
                                 attachments=None):
            await on_event(AgentEvent("agent_start", {}))
            await on_event(AgentEvent("agent_end", {"stopReason": "stop"}))

    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path),
                 service=FakeService(), event_log=FakeLog())
    handle = RunHandle(run_id="r1", session_key="agent:main:dev", abort=asyncio.Event())
    await gw._run(handle, "hi")
    assert [t for _, _, t in recorded] == ["agent_start", "agent_end"]
    assert all(rid == "r1" for _, rid, _ in recorded)
