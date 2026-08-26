"""BundleLayout — where this bundle's own asset directories are, said once.

There are TWO composition roots for these tools: ``agent_authoring_plugin.register`` (the daemon
loads it) and ``presentation/mcp_server`` (a user's own Claude Code connects to it). Both need
the same two paths, and both used to compute them from their own ``parents[n]``, which is two
copies of one fact about the product's layout.

That is fine until one of them moves. ``SKELETON_ROOT`` in particular is load-bearing: it is the
app every scaffolded agent IS, and a scaffold that reads the wrong directory produces an agent
with no window at all — or worse, one carrying a stale SDK that talks a protocol the daemon no
longer speaks.

Not in ``domain/``: this is knowledge of where files sit on disk in a shipped product, which is a
composition concern. It lives at the bundle root so both roots import it as a peer.
"""

from __future__ import annotations

from pathlib import Path


class BundleLayout:
    """The directories this bundle reads its own assets from."""

    #: agents/agent-builder/ — this file is at <that>/plugins/agent-authoring/agent_authoring/.
    AGENT_BUILDER_DIR = Path(__file__).resolve().parents[3]

    #: The whole-app templates and the component catalogue's own files.
    TEMPLATE_ROOT = AGENT_BUILDER_DIR / "skills" / "build-agent" / "templates"

    #: THE SKELETON: a complete, working agent window, copied wholesale into every agent that gets
    #: one. Load-bearing in the same way BORROW_ROOT is, and more so — it is not a place files are
    #: borrowed FROM, it is the artifact an agent starts as. A scaffold that reads the wrong
    #: directory produces an agent with no window at all.
    #:
    #: It replaced `agents/samples/` as the thing that carries structure. Samples had to be read to
    #: help; this is copied whether or not anybody reads it.
    SKELETON_ROOT = TEMPLATE_ROOT / "_skeleton"

    #: The template VARIANTS: one folder per window shape, holding ONLY the files that differ
    #: from the skeleton. Assembly is base + variant overlay + _common, in that order — so the
    #: base is written once and a variant is as small as one App.tsx.
    VARIANTS_ROOT = TEMPLATE_ROOT / "_variants"
    #: The shared modules copied into every agent's ``app/src/common/`` — accounts and money.
    #: Load-bearing for the same reason BORROW_ROOT is: the scaffolder copies FROM here and the
    #: validator compares AGAINST here, so if the two ever disagreed every agent would validate
    #: as modified the moment it was created.
    COMMON_ROOT = TEMPLATE_ROOT / "_common"
