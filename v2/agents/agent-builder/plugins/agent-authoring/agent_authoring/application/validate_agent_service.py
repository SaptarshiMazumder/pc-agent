"""ValidateAgentService — the use case: "check this agent and tell me everything wrong with it".

Pure orchestration. It owns the ORDER of the work and nothing else: read the directory once,
hand the same snapshot to every rule, collect findings into a Report. It performs no I/O itself
(the reader does) and judges nothing itself (the rules do), so adding a rule is a constructor
argument, never a change here.

The one piece of flow control worth naming: if the directory does not parse into an AgentSpec
there is no point running rules that expect one, so that single ERROR is returned alone. A wall
of downstream noise caused by one broken bracket helps nobody.
"""

from __future__ import annotations

from ..domain.finding import ERROR, Finding
from ..domain.report import Report


class ValidateAgentService:
    def __init__(
        self,
        reader,
        layout_rules,
        packageability_rules,
        sandbox_rules,
        ui_rules=None,
        tool_grant_rules=None,
        portability_rules=None,
        declaration_rules=None,
        freshness_rules=None,
        common_rules=None,
    ):
        self._reader = reader
        self._layout = layout_rules
        self._packageability = packageability_rules
        self._sandbox = sandbox_rules
        # Optional for the same reason as ui_rules: a caller that does not inject it simply
        # does not get that check, rather than failing to construct.
        self._grants = tool_grant_rules
        self._portability = portability_rules
        # [[settings]] / [[mcp]] / [[oauth]] coherence. Optional like the two below: a caller
        # that does not inject it simply does not get the check.
        self._declarations = declaration_rules
        # Optional so the service still constructs where the runtime's event/method vocabulary
        # is not available to inject (unit tests). Absent => the app code simply is not read.
        self._ui = ui_rules
        # Is the built ui/ older than the app/src it came from? The only rule that needs file
        # TIMES rather than file content, so it is the only one handed a different snapshot.
        self._freshness = freshness_rules
        self._common = common_rules

    def validate(self, agent_id: str) -> Report:
        agent_id = (agent_id or "").strip()
        if not agent_id:
            return self._single("", ERROR, "NO_AGENT_ID", "validate_agent needs an `agent_id`", "")

        spec = self._reader.spec(agent_id)
        if spec is None:
            known = ", ".join(sorted(self._reader.known_ids())) or "none"
            return self._single(
                agent_id,
                ERROR,
                "UNKNOWN_AGENT",
                f"no agent '{agent_id}' in the registry (known: {known})",
                fix="check the id, or call reload_agent if you just created it",
            )

        agent_dir = self._reader.agent_dir(agent_id)
        if agent_dir is None or not agent_dir.is_dir():
            return self._single(
                agent_id,
                ERROR,
                "NO_AGENT_DIR",
                f"'{agent_id}' resolves to no directory on disk — it may be the synthesized "
                f"default agent rather than an authored one",
                fix="author it under the agents dir first (create_agent)",
            )

        # ONE read, shared by every rule — rules never touch the filesystem.
        raw = self._reader.raw(agent_dir)
        files = self._reader.files(agent_dir)
        if not raw:
            return self._single(
                agent_id,
                ERROR,
                "BAD_AGENT_TOML",
                "agent.toml is missing or is not valid TOML",
                path="agent.toml",
                fix="every top-level key must appear BEFORE the first [table]",
            )

        findings: list[Finding] = []
        sources = self._reader.sources(agent_dir, files)  # read once, shared by every rule
        findings += self._layout.check(spec, raw, files)
        findings += self._packageability.check(spec, raw, files)
        findings += self._sandbox.check(spec, raw, files, sources)
        if self._grants is not None:
            findings += self._grants.check(spec, raw, files)
        if self._portability is not None:
            findings += self._portability.check(spec, raw, files)
        if self._declarations is not None:
            findings += self._declarations.check(spec, raw, files, sources)
        if self._ui is not None:
            findings += self._ui.check(spec, raw, files, sources)
        if self._common is not None:
            findings += self._common.check(spec, raw, files, sources)
        if self._freshness is not None:
            findings += self._freshness.check(spec, raw, files, self._reader.mtimes(agent_dir, files))
        # Checks emit what they detect; the RULEBOOK decides what each code weighs. One
        # repricing seam, so severity policy is edited in the table, never in a rule module.
        from ..domain.rulebook import apply_policy

        return Report(agent_id=agent_id, findings=apply_policy(tuple(findings)))

    @staticmethod
    def _single(agent_id, level, code, message, path="", fix="") -> Report:
        return Report(
            agent_id=agent_id,
            findings=(Finding(level=level, code=code, message=message, path=path, fix=fix),),
        )
