"""Cabbie's authoring tools — Agent Builder's `agent_authoring` package, imported not copied.

This shim's only job is to make that package importable, then hand off to its composition root.
One source of truth: a fix to create_agent / validate / build reaches both agents, because there
is no second copy.

WHY A DISTINCT MODULE NAME. The plugin loader imports the entry module by bare name and Python
caches it in ``sys.modules``, so two bundles that both shipped ``agent_authoring_plugin`` would
collide — the second load would silently get the first. Cabbie's entry is ``cloud_authoring_plugin``
(this file); the shared PACKAGE (``agent_authoring``) keeps its name and is imported once.

WHY A FILESYSTEM PATH, NOT THE REGISTRY. On a hosted daemon Agent Builder is withheld
(``requires_local``), so ``registry.resolve_dir("agent-builder")`` returns None — cabbie cannot ask
the roster where AB is. But AB's FILES sit on disk beside cabbie's, both under the agents root, and
must be present in the image even though the agent is never served. So the package is located by
walking to the sibling folder, with a clear error if it is not there.

FENCING IS NOT HERE. This shim registers AB's FULL toolset; cabbie's ``agent.toml`` removes the
unsafe ones (create_tool, package_agent, publish_agent, run_agent) with a deny-list and grants no
shell. The boundary is one declarative place, not a second forkable copy of AB's composition root.
"""

from __future__ import annotations

import sys
from pathlib import Path


def register(api, ctx):
    # <agents>/cloud-agent-builder/plugins/cloud-agent-authoring/cloud_authoring_plugin.py
    #   parents[0] cloud-agent-authoring   parents[1] plugins
    #   parents[2] cloud-agent-builder     parents[3] <agents root>
    agents_root = Path(__file__).resolve().parents[3]
    ab_plugin = agents_root / "agent-builder" / "plugins" / "agent-authoring"
    if not (ab_plugin / "agent_authoring_plugin.py").is_file():
        raise RuntimeError(
            "Cloud Agent Builder needs Agent Builder's authoring package, expected beside it at "
            f"{ab_plugin}. Those files must ship next to cabbie even on a hosted daemon that never "
            "serves Agent Builder itself. They are missing."
        )
    # Put AB's plugin dir on the path so `import agent_authoring...` (and AB's own entry module,
    # which does that at import time) resolves to the one shared package.
    if str(ab_plugin) not in sys.path:
        sys.path.insert(0, str(ab_plugin))

    from agent_authoring_plugin import register as _authoring_register

    _authoring_register(api, ctx)
