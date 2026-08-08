"""Contract tests for plugin stdout capture at the sandbox boundary (plan item 5.4).

The gap being closed: `agentd_telemetry.redact` guarantees nothing WE log can carry user content,
because every field passes an allowlist. A third-party plugin's `print()` was never in that path —
it writes to the same stdout and reaches CloudWatch verbatim.

What these pin:
  * a plugin's print does NOT reach the real stdout
  * secret-shaped text is masked, and a print-loop cannot fill a log group
  * OUR telemetry still reaches the real stdout while a plugin is running (the trap: the redirect
    is process-global, so the naive version swallows our own metrics)
  * output produced before an exception is still captured — that is the output that explains it
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from agent_runtime.application.interfaces.tool import ToolResult
from agent_runtime.domain.sandbox import DENY_ALL
from agent_runtime.infrastructure.tools.sandbox import stdout_capture
from agent_runtime.infrastructure.tools.sandbox.local import LocalPluginSandbox


class _PrintingTool:
    name = "leaky"

    def __init__(self, text: str, boom: bool = False) -> None:
        self._text = text
        self._boom = boom

    async def execute(self, _call_id, _params, _abort, _on_update=None):
        print(self._text)
        if self._boom:
            raise RuntimeError("plugin blew up after printing")
        return ToolResult.text("done")


def _run(coro):
    return asyncio.run(coro)


def test_a_plugins_print_does_not_reach_the_real_stdout(capsys):
    sandbox = LocalPluginSandbox()
    tool = _PrintingTool("MY_PLUGIN_DEBUG_LINE")
    sandbox.register("evil-plugin", "leaky", tool)

    result = _run(sandbox.run_tool("evil-plugin", "leaky", "c1", {}, None, grant=DENY_ALL))
    assert result.is_error is False

    out = capsys.readouterr().out
    assert "MY_PLUGIN_DEBUG_LINE" not in out, "raw plugin output must never hit the log stream"


def test_our_own_metrics_still_reach_stdout_while_a_plugin_runs(capsys, monkeypatch):
    """THE TRAP. `contextlib.redirect_stdout` is process-global and cannot tell the plugin's
    `print` from `emf.emit`'s write, so the naive version makes our telemetry disappear for
    exactly the duration of third-party code."""
    monkeypatch.setenv("AGENTD_TELEMETRY", "1")
    from agentd_telemetry import count

    class _EmittingTool(_PrintingTool):
        async def execute(self, _call_id, _params, _abort, _on_update=None):
            print("PLUGIN_NOISE")
            count("tool_call_total", outcome="ok")  # our own metric, mid-plugin
            return ToolResult.text("done")

    sandbox = LocalPluginSandbox()
    sandbox.register("p", "leaky", _EmittingTool(""))
    _run(sandbox.run_tool("p", "leaky", "c1", {}, None, grant=DENY_ALL))

    out = capsys.readouterr().out
    assert "PLUGIN_NOISE" not in out
    assert "tool_call_total" in out, "our metrics must survive a plugin call"


def test_the_capture_is_restored_afterwards():
    sandbox = LocalPluginSandbox()
    sandbox.register("p", "leaky", _PrintingTool("x"))
    before = sys.stdout
    _run(sandbox.run_tool("p", "leaky", "c1", {}, None, grant=DENY_ALL))
    assert sys.stdout is before


def test_output_printed_before_an_exception_is_still_captured(caplog):
    """That output is usually the thing that explains the exception."""
    sandbox = LocalPluginSandbox()
    sandbox.register("p", "leaky", _PrintingTool("LAST_WORDS", boom=True))
    with caplog.at_level("INFO"), pytest.raises(RuntimeError):
        _run(sandbox.run_tool("p", "leaky", "c1", {}, None, grant=DENY_ALL))
    assert any("LAST_WORDS" in str(r.__dict__.get("plugin_output", "")) for r in caplog.records)


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnopqrstuvwx",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_abcdefghijklmnopqrstuvwxyz01",
        "xoxb-1234567890-abcdefghij",
        "api_key = hunter2supersecret",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ],
)
def test_secret_shaped_text_is_masked(secret):
    scrubbed = stdout_capture.scrub_text(f"calling api with {secret} now")
    assert secret not in scrubbed
    assert "[redacted]" in scrubbed


def test_a_print_loop_cannot_fill_a_log_group():
    scrubbed = stdout_capture.scrub_text("\n".join(f"line {i}" for i in range(500)))
    kept = scrubbed.splitlines()
    assert len(kept) == stdout_capture.MAX_LINES + 1  # +1 for the "N more suppressed" marker
    assert "more line(s) suppressed" in kept[-1]
    assert len(scrubbed) <= stdout_capture.MAX_TOTAL_CHARS


def test_a_single_enormous_line_is_truncated():
    scrubbed = stdout_capture.scrub_text("A" * 10_000)
    assert len(scrubbed) <= stdout_capture.MAX_LINE_CHARS + 1  # + the ellipsis


def test_capture_can_be_turned_off(monkeypatch, capsys):
    """An escape hatch for someone debugging their own plugin locally — and proof that the
    default is what makes the difference, not some other layer."""
    monkeypatch.setenv("AGENTD_PLUGIN_STDOUT_CAPTURE", "0")
    sandbox = LocalPluginSandbox()
    sandbox.register("p", "leaky", _PrintingTool("UNCAPTURED_LINE"))
    _run(sandbox.run_tool("p", "leaky", "c1", {}, None, grant=DENY_ALL))
    assert "UNCAPTURED_LINE" in capsys.readouterr().out


def test_the_buffer_stops_collecting_long_before_memory_matters():
    buffer = stdout_capture._Tee()
    for _ in range(1000):
        buffer.write("x" * 1000)
    assert len(buffer.value()) <= stdout_capture.MAX_TOTAL_CHARS * 4 + 1000
