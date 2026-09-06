"""e2e_replay — re-diagnose a saved trace. Free: no daemon run, no model, no backend.

A live run is expensive (real model, real GPU); its trace is not. Replay is how the fix loop
re-reads a run — after a signals-engine change, to compare two models' traces on one scenario,
or just to re-open the last report without paying for it again.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class E2eReplayTool(Tool):
    name = "e2e_replay"
    label = "Replay E2E Trace"
    default_retryable = True  # pure read
    description = (
        "RE-DIAGNOSE A SAVED E2E TRACE (the `.trace.jsonl` e2e_run writes beside the scenario) "
        "without re-running anything — no model spend, no backend. Same report as e2e_run: "
        "transcript, findings with agent/environment origin, triage, and (when scenario_path is "
        "given) the checks. Use it to re-read a run, or to diff two models' traces on one "
        "scenario turn by turn."
    )
    parameters = {
        "type": "object",
        "required": ["trace_path"],
        "properties": {
            "trace_path": {
                "type": "string",
                "description": "path to the saved .trace.jsonl",
            },
            "scenario_path": {
                "type": "string",
                "description": "the scenario JSON, to also re-run its checks (recommended)",
            },
        },
    }

    def __init__(self, ctx):
        self._ctx = ctx

    async def execute(self, tool_call_id, params, abort, on_update=None):
        from agent_runtime.e2e import checks as checks_mod
        from agent_runtime.e2e import report as report_mod
        from agent_runtime.e2e import signals
        from agent_runtime.e2e.scenario import Scenario
        from agent_runtime.e2e.trace import load_trace

        raw = str(params.get("trace_path") or "").strip()
        if not raw:
            return ToolResult.text("e2e_replay needs `trace_path`", is_error=True)
        path = Path(raw)
        if not path.is_file():
            return ToolResult.text(f"trace not found: {path}", is_error=True)

        try:
            trace = load_trace(path)
        except (ValueError, OSError) as e:
            return ToolResult.text(f"could not read trace {path}: {e}", is_error=True)

        scenario = None
        sp = str(params.get("scenario_path") or "").strip()
        if sp:
            try:
                scenario = Scenario.load(sp)
            except (ValueError, KeyError, OSError) as e:
                return ToolResult.text(f"could not load scenario {sp}: {e}", is_error=True)

        findings = signals.diagnose(trace)
        results = checks_mod.run_checks(trace, scenario.checks) if scenario else []
        return ToolResult.text(
            report_mod.render(trace, findings, results,
                              goal=scenario.goal if scenario else "")
        )
