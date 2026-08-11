"""Where the CURRENT RUN may write. One rule, one place, every writer.

An agent's write scope is declared in its ``agent.toml`` (``[tools.fs] write_roots`` / ``deny``),
expanded to absolute paths by ``AgentService``, and carried on the ``RunContext``. This module is
the only thing that decides whether a path is inside it.

IT LIVES HERE, NOT IN A PLUGIN, because the `write` tool is not the only writer. `create_tool`,
`create_agent` and the UI scaffolder all open files themselves — and `create_tool` without an
``agent`` writes into the SHARED ``plugins/`` directory, which is precisely the path the scope
exists to close. A check that lived in ``core_fs`` would guard the front door of a building with
three others.

EMPTY ROOTS = UNRESTRICTED. Every agent that declares nothing behaves exactly as it always has;
only one that opts in is constrained. This is a scope for the agent whose reach is unusual, not
a new default for everybody.

READS ARE NEVER SCOPED. Reading damages nothing, and an agent must be able to read its own skill
and the SDK it vendors into a generated UI.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_runtime.application.run_context import current_run_context


class WriteRefused(Exception):
    """This run may not write there. The message is written FOR THE MODEL — it names the
    legitimate alternative, because a refusal that only says no gets retried."""


def is_inside(child, parent: str) -> bool:
    """Is ``child`` at or below ``parent``?

    Both sides go through ``realpath`` first. Without it, ``roots/../../plugins`` and a symlink
    pointing out of the root both read as inside, and the check is decorative. Resolving turns
    the question into prefix containment over the paths the OS will actually use.

    The separator on the end matters: a bare ``startswith`` puts ``/x/agents-backup`` inside
    ``/x/agents``."""
    try:
        c = os.path.realpath(str(child))
        p = os.path.realpath(str(parent))
    except (OSError, ValueError):
        return False
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def check_write(path) -> Path:
    """Return ``path`` if this run may write it; raise ``WriteRefused`` otherwise.

    DENY BEATS ALLOW, so an agent can be handed a wide root and still be kept out of its own
    definition. Without that, an agent able to rewrite its own ``agent.toml`` could widen its own
    roots, and none of this would mean anything."""
    p = Path(path)
    ctx = current_run_context()
    if ctx is None:
        return p
    roots = tuple(getattr(ctx, "write_roots", ()) or ())

    # Checked FIRST, and it applies even to an agent with no declared roots: "do not edit
    # someone else's installed agent" is not a scope its author chose, it is the platform's.
    for protected in tuple(getattr(ctx, "protected_paths", ()) or ()):
        if is_inside(p, protected):
            raise WriteRefused(
                f"refusing to write {p}: that agent was INSTALLED from a package. Editing it "
                f"would leave it no longer matching what its publisher shipped, while still "
                f"carrying their name. If you want to change how it behaves, build your own "
                f"agent — do not edit theirs."
            )

    for denied in tuple(getattr(ctx, "write_denies", ()) or ()):
        if is_inside(p, denied):
            raise WriteRefused(
                f"refusing to write {p}: inside `{denied}`, which this agent is denied. That is "
                f"its own definition — an agent does not edit the rules it is running under."
            )

    if not roots:
        return p  # declared no scope -> unrestricted, apart from the protected set above
    if not any(is_inside(p, r) for r in roots):
        raise WriteRefused(
            f"refusing to write {p}: outside this agent's write scope "
            f"({', '.join(roots)}).\n"
            f"If you are authoring an agent, write under that agent's own directory. If you want "
            f"a SHARED tool or a file elsewhere, that is the USER's decision — ask them, and do "
            f"NOT reach for `exec` to get around this."
        )
    return p
