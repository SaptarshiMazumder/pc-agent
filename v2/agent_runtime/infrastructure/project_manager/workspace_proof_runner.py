"""WorkspaceProofRunner — checks a contract criterion itself, through the run's own tools.

Commands and scenarios go through the SAME `exec` and `e2e_run` tools the developer holds, so a
proof runs as the same caller, in the same sandbox, against the same files — the check cannot pass
on a machine the developer's work never reached, or fail on one it did. Files are read from the
run's workspace (relative paths) or as given (absolute).

A proof that cannot run says so as evidence (NOT PROVEN, "no exec tool in this run"), never passes
by default.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from agent_runtime.application.run_context import current_workspace
from agent_runtime.domain.deliverable_contract import (
    ARTIFACT_PRODUCED,
    COMMAND_SUCCEEDS,
    FILE_CONTAINS,
    JUDGED,
    SCENARIO_PASSES,
    Criterion,
)
from agent_runtime.domain.proof_result import ProofResult

EVIDENCE_CHARS = 400
COMMAND_TIMEOUT_S = 600
# The e2e report's own summary line (agent_runtime/e2e/report.py): "VERDICT: 3/3 checks passed".
_VERDICT = re.compile(r"VERDICT:\s*(\d+)/(\d+) checks passed")


class WorkspaceProofRunner:
    def __init__(self, tools: list) -> None:
        self._tools = {t.name: t for t in tools}

    async def prove(self, criterion: Criterion) -> ProofResult:
        kind, spec = criterion.proof.kind, criterion.proof.spec
        if kind == JUDGED:
            return ProofResult(criterion.id, None, "no machine check; judge from what ran")
        if kind == ARTIFACT_PRODUCED:
            return self._artifact(criterion.id, str(spec.get("glob") or ""))
        if kind == FILE_CONTAINS:
            return self._contains(criterion.id, str(spec.get("path") or ""), str(spec.get("text") or ""))
        if kind == COMMAND_SUCCEEDS:
            return await self._command(criterion.id, str(spec.get("command") or ""), str(spec.get("expect") or ""))
        if kind == SCENARIO_PASSES:
            return await self._scenario(criterion.id, str(spec.get("scenario_path") or ""))
        return ProofResult(criterion.id, False, f"unknown proof kind {kind!r}")

    # ------------------------------------------------------------------ files

    def _artifact(self, cid: str, pattern: str) -> ProofResult:
        if not pattern:
            return ProofResult(cid, False, "the proof names no glob")
        base, rel = _split_glob(pattern)
        hits = sorted(str(p) for p in base.glob(rel) if p.is_file())[:5]
        if hits:
            return ProofResult(cid, True, f"found {', '.join(hits)}")
        return ProofResult(cid, False, f"nothing matches {pattern} (searched {base})")

    def _contains(self, cid: str, path: str, text: str) -> ProofResult:
        target = _resolve(path)
        if not target.is_file():
            return ProofResult(cid, False, f"{target} does not exist")
        body = target.read_text(encoding="utf-8", errors="replace")
        if text in body:
            return ProofResult(cid, True, f"{target} contains {text!r}")
        return ProofResult(cid, False, f"{target} exists but does not contain {text!r}")

    # ------------------------------------------------------------------ through the run's tools

    async def _command(self, cid: str, command: str, expect: str) -> ProofResult:
        if not command:
            return ProofResult(cid, False, "the proof names no command")
        ok, output = await self._call("exec", {"command": command, "timeout_sec": COMMAND_TIMEOUT_S})
        if ok is None:
            return ProofResult(cid, False, output)
        if not ok:
            return ProofResult(cid, False, f"`{command}` failed: {_tail(output)}")
        if expect and expect not in output:
            return ProofResult(cid, False, f"`{command}` ran but its output lacks {expect!r}: {_tail(output)}")
        return ProofResult(cid, True, f"`{command}` succeeded: {_tail(output)}")

    async def _scenario(self, cid: str, scenario_path: str) -> ProofResult:
        if not scenario_path:
            return ProofResult(cid, False, "the proof names no scenario")
        ok, output = await self._call("e2e_run", {"scenario_path": scenario_path})
        if ok is None:
            return ProofResult(cid, False, output)
        m = _VERDICT.search(output)
        if not ok or m is None:
            return ProofResult(cid, False, f"the scenario did not produce a verdict: {_tail(output)}")
        got, total = int(m.group(1)), int(m.group(2))
        return ProofResult(cid, total > 0 and got == total, m.group(0))

    async def _call(self, tool_name: str, params: dict) -> tuple[bool | None, str]:
        tool = self._tools.get(tool_name)
        if tool is None:
            return None, f"no `{tool_name}` tool in this run, so this proof cannot be checked"
        result = await tool.execute(f"manager-proof-{tool_name}", params, asyncio.Event())
        text = "".join(getattr(b, "text", "") for b in (result.content or []))
        return not result.is_error, text


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path(current_workspace(".")) / p


def _split_glob(pattern: str) -> tuple[Path, str]:
    """An absolute glob searches from its fixed prefix; a relative one from the workspace."""
    p = Path(pattern)
    if not p.is_absolute():
        return Path(current_workspace(".")), pattern
    parts = p.parts
    fixed = next((i for i, part in enumerate(parts) if any(c in part for c in "*?[")), len(parts) - 1)
    return Path(*parts[:fixed]), str(Path(*parts[fixed:]).as_posix())


def _tail(text: str) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= EVIDENCE_CHARS else "…" + flat[-EVIDENCE_CHARS:]


__all__ = ["WorkspaceProofRunner"]
