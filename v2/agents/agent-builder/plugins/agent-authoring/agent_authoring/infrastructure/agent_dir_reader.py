"""AgentDirReader — the ONLY thing in this bundle that touches the filesystem.

It gathers what the pure rules need and nothing more:
    spec()    the parsed AgentSpec
    raw()     agent.toml as a plain dict (rules inspect keys the AgentSpec has already folded away)
    files()   every file in the agent dir, as agent-relative POSIX paths
    sources() the text of each private plugin module (for SandboxRules' source scan)

ONE SOURCE OF TRUTH: parsing is DELEGATED to the core registry (``FileAgentRegistry``), never
re-implemented here. If a directory yields an AgentSpec it is structurally valid, by definition —
the same definition the daemon uses at startup. A second agent.toml reader in this bundle would
drift, and then "valid" would quietly mean two different things.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

# Never walk into these — they are noise (caches, VCS) or excluded from packing anyway.
SKIP_DIRS = frozenset({"__pycache__", ".git", ".pytest_cache", "node_modules", ".agentd"})
MAX_SOURCE_BYTES = 256 * 1024  # a plugin module larger than this is not worth scanning


class AgentDirReader:
    def __init__(self, registry):
        self._registry = registry

    # ------------------------------------------------------------------ spec
    def agent_dir(self, agent_id: str) -> Path | None:
        spec = self.spec(agent_id)
        d = getattr(spec, "dir", None) if spec is not None else None
        return Path(d) if d else None

    def spec(self, agent_id: str):
        """The parsed AgentSpec, or None when the registry does not know this agent."""
        try:
            return self._registry.get(agent_id)
        except (KeyError, Exception):  # noqa: BLE001 — unknown agent is a caller-visible outcome
            return None

    def known_ids(self) -> list[str]:
        try:
            return list(self._registry.list_ids())
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------ files
    def raw(self, agent_dir: Path) -> dict:
        """agent.toml as a dict. {} when absent/unreadable — the rules report that themselves."""
        path = agent_dir / "agent.toml"
        try:
            return tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def files(self, agent_dir: Path) -> list[str]:
        """Every file under the agent dir as a relative POSIX path, caches skipped."""
        out: list[str] = []
        for p in agent_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(agent_dir)
            if any(part in SKIP_DIRS for part in rel.parts[:-1]):
                continue
            out.append(rel.as_posix())
        return sorted(out)

    def sources(self, agent_dir: Path, files: list[str]) -> dict:
        """Text of the agent's own CODE, for the rules that read it:

            plugins/**/*.py         the sandbox source scan (secrets / network reach)
            plugins/**/plugin.toml  what that plugin DECLARED it needs ([sandbox] net/secrets) —
                                    without it the network rule cannot tell a plugin doing the
                                    right thing from one reaching out with no declaration, and
                                    would warn about the fix
            ui/**/*.js              the app checks (event shapes, callable methods)

        The vendored SDK under ui/vendor/ is included — the UI rules skip it themselves, and
        reading it is how a check could later confirm which methods really exist.

        Unreadable or oversized files are skipped: a source scan is guidance, never a gate."""
        out: dict[str, str] = {}
        for rel in files:
            wanted = (
                rel.startswith("plugins/") and (rel.endswith(".py") or rel.endswith("plugin.toml"))
            ) or (rel.startswith("ui/") and rel.endswith(".js"))
            if not wanted:
                continue
            p = agent_dir / rel
            try:
                if p.stat().st_size > MAX_SOURCE_BYTES:
                    continue
                out[rel] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        return out
