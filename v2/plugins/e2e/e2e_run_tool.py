"""e2e_run — drive one e2e scenario against an agent on THIS daemon, return the diagnosis.

This is `run_agent`'s bigger sibling: not one message but a whole scripted user (ordered turns,
reference media, settings) with a diagnosis at the end instead of a reply. The transport is the
in-process gateway client — the same chat.send/config.set/workspace.upload a socket client
speaks, carried as the CALLER's account with no socket, which is what makes it equally real on a
desktop and a hosted daemon (where a socket dial-back's ?act_as= does not authorise).

ISOLATION IS THE TOOL'S JOB. Every run uses a fresh throwaway session key (`e2e:<agent>:<hex>`)
and deletes that session afterwards, so a test never lands in anyone's chat list. Settings writes
are account-scoped (the caller's own config), never machine or someone else's.

TRIAGE IS THE REPORT'S JOB. Findings carry an origin — agent vs environment — and the report says
which findings mean "edit the agent" and which mean "retry / fix the resource / ask the user".
Respect it: most live-run failures are environmental, and an agent 'fixed' over a provider 429
is an agent broken.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult

#: A settings value that is a template placeholder, not a value. Running with one burns a real
#: model run on a guaranteed dead backend, so it is refused up front.
_PLACEHOLDER_RX = re.compile(r"PASTE|YOUR_|CHANGE.?ME|FILL.?ME|<[^>]{1,40}>|^\s*$")

#: No event at all for this long = the run is wedged. Generous: a model download or long render
#: emits nothing while it works.
DEFAULT_IDLE_TIMEOUT_S = 900


class E2eRunTool(Tool):
    name = "e2e_run"
    label = "Run E2E Scenario"
    concurrency = "sequential"  # it runs another agent; do not fan these out
    default_timeout_sec = None  # a scenario is many agent turns; it manages its own idle guard
    default_retryable = False  # side-effecting: real model spend, real backend work
    description = (
        "RUN AN E2E SCENARIO against an agent on this daemon and get the full diagnosis: "
        "transcript, findings (thrash/stall/holes/errors, each tagged agent vs environment), "
        "TRIAGE, and the scenario's checks. Use it after building or changing an agent — "
        "`run_agent` proves one message works; this proves the agent does its JOB.\n"
        "Scenario JSONs live at `agents/<id>/e2e/<name>.json` (see e2e_checks for the check "
        "vocabulary). Before running, read the target's agent.toml settings/secrets and "
        "provision every input the scenario needs: values you can supply ride "
        "`settings_overrides` (written to YOUR account's config for the target agent — never "
        "anyone else's); values only the user has (a real backend URL, a paid key) must be "
        "ASKED for, never invented; if a resource is missing, run the subset that works and say "
        "what was skipped.\n"
        "READ THE TRIAGE BEFORE TOUCHING THE AGENT. Fix ONE agent-origin finding, re-run, "
        "confirm it cleared. Environment findings (429s, drops, unreachable backends, missing "
        "credentials) mean retry / fix the resource / ask the user — never an agent edit.\n"
        "Checks green is NOT done: judge the actual OUTPUT against the scenario goal (look at "
        "the artifact, or have the user look) before calling the agent finished.\n"
        "The run is isolated: a throwaway session, deleted afterwards (keep_session=true to "
        "inspect it). The trace is saved beside the scenario for e2e_replay."
    )
    parameters = {
        "type": "object",
        "required": ["scenario_path"],
        "properties": {
            "scenario_path": {
                "type": "string",
                "description": "path to the scenario JSON (absolute, or relative to the agents "
                "root), e.g. agents/<id>/e2e/basic-job.json",
            },
            "model": {
                "type": "string",
                "description": "pin a model for the run (account-scoped config.set on the "
                "target agent). Omit to run whatever the agent is configured with",
            },
            "settings_overrides": {
                "type": "object",
                "description": "setting values injected over the scenario's own `settings` for "
                "this run — live URLs and credentials ride here so the committed scenario never "
                "holds them",
            },
            "keep_session": {
                "type": "boolean",
                "description": "keep the test session instead of deleting it (default false) — "
                "only when you deliberately want to inspect the conversation afterwards",
            },
            "idle_timeout_s": {
                "type": "integer",
                "description": f"seconds of TOTAL event silence before the run counts as wedged "
                f"(default {DEFAULT_IDLE_TIMEOUT_S})",
            },
        },
    }

    def __init__(self, ctx):
        self._ctx = ctx

    async def execute(self, tool_call_id, params, abort, on_update=None):
        from agent_runtime.e2e import checks as checks_mod
        from agent_runtime.e2e import report as report_mod
        from agent_runtime.e2e import signals
        from agent_runtime.e2e.live_driver import drive
        from agent_runtime.e2e.scenario import Scenario

        path = self._resolve_path(str(params.get("scenario_path") or "").strip())
        if isinstance(path, str):  # resolution failure message
            return ToolResult.text(path, is_error=True)

        try:
            scenario = Scenario.load(path)
        except (ValueError, KeyError, OSError) as e:
            return ToolResult.text(f"could not load scenario {path}: {e}", is_error=True)

        for k, v in (params.get("settings_overrides") or {}).items():
            scenario.settings[str(k)] = str(v)

        # Refuse placeholders BEFORE spending a real run on a dead backend. This is the
        # provision-step guard: the fix is an override or asking the user — never guessing.
        dead = [k for k, v in scenario.settings.items() if _PLACEHOLDER_RX.search(v)]
        if dead:
            return ToolResult.text(
                "these scenario settings are placeholders, not values: "
                + ", ".join(sorted(dead))
                + ". Provision them first — pass real values via settings_overrides, or ask the "
                "user for the ones only they have (never invent a URL or credential). If the "
                "resource is genuinely unavailable, run a scenario variant that does not need it "
                "and say what was skipped.",
                is_error=True,
            )

        client_thunk = getattr(self._ctx, "gateway_client", None)
        transport = client_thunk() if callable(client_thunk) else None
        if transport is None:
            return ToolResult.text(
                "the gateway is not ready for in-process runs (daemon still starting?) — "
                "this is an environment problem, not the agent's",
                is_error=True,
            )

        if on_update:
            on_update(f"running scenario {scenario.id} against {scenario.agent_id}…")

        out = path.with_suffix(".trace.jsonl")
        idle = float(params.get("idle_timeout_s") or DEFAULT_IDLE_TIMEOUT_S)
        try:
            trace = await drive(scenario, transport, out, model=str(params.get("model") or ""),
                                idle_timeout=idle, progress=on_update)
        except Exception as e:  # noqa: BLE001 — the failure text is the deliverable here
            return ToolResult.text(
                f"the run could not be driven: {e}\n"
                "This failed in TRANSPORT/SETUP, before or between agent turns — an "
                "environment/harness problem, not a finding about the agent. Fix the cause and "
                "re-run; do not edit the agent over this.",
                is_error=True,
            )

        findings = signals.diagnose(trace)
        results = checks_mod.run_checks(trace, scenario.checks)
        text = report_mod.render(trace, findings, results, goal=scenario.goal)

        notes = [f"\ntrace saved: {out} (re-diagnose free with e2e_replay)"]
        if not params.get("keep_session") and trace.session_key:
            try:
                await transport.call("sessions.delete", {
                    "agentId": scenario.agent_id, "sessionKey": trace.session_key,
                })
                notes.append(f"test session {trace.session_key} deleted (isolation)")
            except Exception as e:  # noqa: BLE001 — cleanup failure must not sink the report
                notes.append(f"WARNING: could not delete test session {trace.session_key}: {e}")

        # The report already carries TRIAGE; failing CHECKS is a finding, not a tool error.
        return ToolResult.text(text + "\n" + "\n".join(notes))

    def _resolve_path(self, raw: str):
        """Absolute → as-is; relative → against the agents root, then cwd. Returns a Path, or an
        error STRING naming everything that was tried (so the fix is obvious)."""
        if not raw:
            return "e2e_run needs `scenario_path`"
        p = Path(raw)
        if p.is_absolute():
            return p if p.is_file() else f"scenario not found: {p}"
        tried = []
        agents_dir = getattr(getattr(self._ctx, "config", None), "agents_dir", None)
        if agents_dir:
            base = Path(agents_dir)
            for cand in (base / raw, base.parent / raw):
                if cand.is_file():
                    return cand
                tried.append(str(cand))
        if p.is_file():
            return p
        tried.append(str(p.resolve()))
        return "scenario not found — tried: " + "; ".join(tried)
