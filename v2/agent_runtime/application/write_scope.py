"""Where the CURRENT RUN may write — and, on a hosted daemon, what it may SEE. One rule,
one place, every reader and every writer.

Two kinds of scope, both carried on the ``RunContext`` and decided elsewhere:

* the AGENT's write scope — declared in its ``agent.toml`` (``[tools.fs] write_roots`` /
  ``deny``), expanded to absolute paths by ``AgentService``. The agent's own choice.
* the TENANT scope — ``read_roots`` + ``write_clamp``, assembled per run by
  ``infrastructure/user_state.tenant_scope`` from who is calling and which deployment this
  is. The platform's boundary, not the agent's; an agent cannot widen it by declaring things.

IT LIVES HERE, NOT IN A PLUGIN, because the `write` tool is not the only writer. `create_tool`,
`create_agent` and the UI scaffolder all open files themselves — and `create_tool` without an
``agent`` writes into the SHARED ``plugins/`` directory, which is precisely the path the scope
exists to close. A check that lived in ``core_fs`` would guard the front door of a building with
three others.

EMPTY = UNRESTRICTED, for every scope here. A desktop run carries no tenant scope and most
agents declare no write scope, so the checks pass everything and behaviour is byte-for-byte
what it always was. Only a run that was HANDED values is constrained — the deployment changes
the values, never the code path, which is why there is no ``if hosted`` in this file.

ON READS: this module once said "reads are never scoped — reading damages nothing", and on a
desktop that is true: the machine is the owner's, and an agent must be able to read its own
skill and the SDK it vendors. On a hosted daemon the same freedom is one tenant reading
another's workspace and transcripts out of the shared store. So reads are scoped exactly like
writes — by values on the run, empty meaning unrestricted — and the desktop keeps its truth
because its runs simply carry no values.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_runtime.application.run_context import current_run_context


#: A scope root no real path is ever inside (an embedded NUL cannot appear in a file path on
#: any supported OS, so ``is_inside`` is False for everything). Used where a run must carry a
#: FAIL-CLOSED scope — "nothing visible / nothing writable" — because the empty tuple is
#: already taken to mean "unrestricted" (the desktop degenerate case).
NOTHING = "\0nothing"


class WriteRefused(Exception):
    """This run may not write there. The message is written FOR THE MODEL — it names the
    legitimate alternative, because a refusal that only says no gets retried."""


class ReadRefused(Exception):
    """This run may not see that path. Same principle as ``WriteRefused``: name where the
    run's own files actually are, so the model redirects instead of retrying."""


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


def check_read(path) -> Path:
    """Return ``path`` if this run may see it; raise ``ReadRefused`` otherwise.

    ``read_roots`` is a pure ALLOW-list — no deny half, unlike writes. Writes need one so a
    wide root can still exclude the agent's own definition; reads are assembled from nothing
    but positive grants (``tenant_scope``), so anything outside them was never granted and no
    second list has to be kept in step."""
    p = Path(path)
    ctx = current_run_context()
    if ctx is None:
        return p
    roots = tuple(getattr(ctx, "read_roots", ()) or ())
    if not roots:
        return p  # no tenant scope on this run (desktop) -> unrestricted, as ever
    if not any(is_inside(p, r) for r in roots):
        workspace = getattr(ctx, "workspace", "") or "the run workspace"
        raise ReadRefused(
            f"refusing to access {p}: it is outside this run's visible filesystem. Your own "
            f"files live under your workspace ({workspace}); the agent's shipped files "
            f"(skills, templates) are readable in place. Paths belonging to other users of "
            f"this server do not exist for this run."
        )
    return p


def check_write(path) -> Path:
    """Return ``path`` if this run may write it; raise ``WriteRefused`` otherwise.

    Ordered platform-first: the tenant clamp and install-protection are rules the caller
    cannot opt out of, the deny/roots pair is the agent's own declared scope. DENY BEATS
    ALLOW within that pair, so an agent can be handed a wide root and still be kept out of
    its own definition — without that, an agent able to rewrite its own ``agent.toml`` could
    widen its own roots, and none of this would mean anything."""
    p = Path(path)
    ctx = current_run_context()
    if ctx is None:
        return p

    # THE TENANT CLAMP, first: on a hosted daemon every write must land inside the caller's
    # own slice of the shared store, whatever the agent declared (or didn't). Empty = no
    # clamp = a desktop run, unchanged.
    clamp = tuple(getattr(ctx, "write_clamp", ()) or ())
    if clamp and not any(is_inside(p, r) for r in clamp):
        raise WriteRefused(
            f"refusing to write {p}: on this server, files can only be written inside your "
            f"own space ({', '.join(c for c in clamp if not c.startswith(chr(0)))}). Write "
            f"under your workspace instead."
        )

    roots = tuple(getattr(ctx, "write_roots", ()) or ())

    # Applies even to an agent with no declared roots: "do not edit someone else's installed
    # agent" is not a scope its author chose, it is the platform's.
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
        return p  # declared no scope -> unrestricted, apart from the platform rules above
    if not any(is_inside(p, r) for r in roots):
        raise WriteRefused(
            f"refusing to write {p}: outside this agent's write scope "
            f"({', '.join(roots)}).\n"
            f"If you are authoring an agent, write under that agent's own directory. If you want "
            f"a SHARED tool or a file elsewhere, that is the USER's decision — ask them, and do "
            f"NOT reach for `exec` to get around this."
        )
    return p
