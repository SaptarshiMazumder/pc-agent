"""BundleAgentPacker — the ``AgentPacker`` port over the real packer.

Four lines of adapter, and worth having: the service asks for a shape it can fake in a unit test,
while the only real implementation is exactly the function ``agentd bundle pack`` runs. What ships
inside a product is therefore byte-identical to what gets published to the marketplace.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.infrastructure.marketplace import packer


class BundleAgentPacker:
    def pack(self, agent_dir: Path, out_dir: Path, version: str = "") -> Path:
        return packer.pack_agent_dir(Path(agent_dir), Path(out_dir), version)
