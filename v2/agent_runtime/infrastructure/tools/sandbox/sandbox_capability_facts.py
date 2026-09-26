"""SandboxCapabilityFacts — what a command can do in this run, stated by the sandbox package itself.

The same decision `exec` makes (plugins/shell/exec_tool.shell_route): a run with a tenant fence is
hosted — foreground commands go to the executor's microVM (microvm_backend.run_shell, image built
from the daemon's own), background ones to the confined shell (confined_command.py). A run without
one is the owner's own machine. Kept here, beside the code it describes, so a change to either
backend is a change to this file in the same diff — and the manager's picture moves with it.

KNOWN LIMITS belong in `limitations()` while they are true and leave with the fix. A limit listed
here is what lets the manager say "that is the platform, go around it" rather than blaming the
developer — and a limit left here after its fix would make it excuse a real mistake.
"""

from __future__ import annotations

from agent_runtime.application.run_context import current_run_context
from agent_runtime.domain.capability_sheet import CapabilityFact, KnownLimitation


class SandboxCapabilityFacts:
    def __init__(self, config) -> None:
        self._config = config

    def facts(self) -> list[CapabilityFact]:
        ctx = current_run_context()
        if ctx is None or not getattr(ctx, "read_roots", ()):
            return [CapabilityFact("commands", "`exec` runs on the owner's own machine, in its own shell, "
                                               "with whatever that machine has installed.")]
        out: list[CapabilityFact] = []
        if str(getattr(self._config, "executor_url", "") or "").strip():
            out.append(CapabilityFact(
                "commands",
                "Foreground `exec` runs each command on a fresh, throwaway Linux x86_64 microVM with "
                "outbound internet: sh, bash, tar, gzip, Python 3 and pip, and the daemon's own Python "
                "packages (boto3 among them) import directly. No curl, wget, unzip, git or node — but a "
                "command can download a pinned release (e.g. a CLI's linux_amd64 zip) with Python, unpack "
                "it into $TMPDIR and run it in the SAME command. So a tool not being preinstalled is never "
                "a blocker. The agent's files are copied in before each command and its changes — "
                "deletions included — copied back after; nothing else survives between commands. "
                "15-minute limit per command.",
            ))
        else:
            out.append(CapabilityFact(
                "commands",
                "Foreground `exec` is unavailable on this server (no executor configured); only "
                "background commands run.",
            ))
        out.append(CapabilityFact(
            "commands",
            "Background `exec` (background=true) with `process` runs on the server, confined to the "
            "user's own files, with no time limit; it installs its own tools the same way.",
        ))
        return out

    def limitations(self) -> list[KnownLimitation]:
        return []


__all__ = ["SandboxCapabilityFacts"]
