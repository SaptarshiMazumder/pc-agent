"""BundleLayout — where this bundle's own asset directories are, said once.

There are TWO composition roots for these tools: ``agent_authoring_plugin.register`` (the daemon
loads it) and ``presentation/mcp_server`` (a user's own Claude Code connects to it). Both need
the same two paths, and both used to compute them from their own ``parents[n]``, which is two
copies of one fact about the product's layout.

That is fine until one of them moves. ``BORROW_ROOT`` in particular is load-bearing: it is the
single source of ``md.js`` and the vendored SDK that every scaffolded agent is built from, and a
scaffold that reads the wrong directory fails with "cannot borrow" — or worse, borrows a stale
SDK that talks a protocol the daemon no longer speaks.

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

    #: The single copy of the files templates and components BORROW — ``md.js`` and the
    #: vendored SDK.
    #:
    #: This was Agent Builder's own live ``ui/`` while that folder was hand-written vanilla JS.
    #: ``ui/`` is now the BUILD OUTPUT of ``app/`` (React + Vite), and a build empties its output
    #: directory — so the one copy every scaffolded agent is built from would be destroyed by an
    #: unrelated ``npm run build``. Borrowing is a real dependency, so it has a real home now:
    #: beside the templates that borrow from it, owned by nothing else.
    BORROW_ROOT = TEMPLATE_ROOT / "_borrowed"
