"""Installing one agent's DEFINITION into an organization's shared layer.

THE ONE IMPLEMENTATION, because there are now three callers and they must write byte-identical
folders: the gateway's direct share (`agents.shareToOrg`), the approval of a member submission,
and the `publish_agent` tool when its destination is the caller's org. Before this module the
logic was a private static method on the gateway, which a tool cannot reach without importing the
whole presentation layer — so the third caller would have grown a second copy, and two copies of
"what an org install is" is exactly how one of them quietly stops stamping provenance.

THE DEFINITION TRAVELS, THE DATA STAYS. Only `definition_entries` (the folder minus
USER_DATA_DIRS) are copied — the same view the tenant fence grants strangers — so the author's
sessions and workspace can never ride along into the whole company's read scope.

ATOMIC BY RENAME. Everything lands in a `.installing` sibling and is renamed over the target only
once it is complete, so a crash mid-copy leaves the org's previous copy intact rather than a
half-written agent every member resolves.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.domain import ownership


def install_org_definition(
    source: Path, target: Path, org_id: str, agent_id: str, author: str = ""
) -> str:
    """Copy an agent's definition into ``target`` as the org's own installed copy.

    Returns "" on success, or a human-readable error string (never raises for IO — the callers
    are RPC handlers and a tool, both of which report rather than crash).

    ``author`` is the account id of whoever contributed this copy. It rides the record because
    ``owner`` is the ORG here, which would otherwise erase the maker: it is the only surviving
    trace of who built the agent, and it is what an org roster's "by <someone>" byline reads.
    """
    import shutil

    from agent_runtime.domain.agent import definition_entries
    from agent_runtime.infrastructure.agents import ownership_store

    source = Path(source)
    target = Path(target)
    staged = target.with_name(target.name + ".installing")
    try:
        shutil.rmtree(staged, ignore_errors=True)
        staged.mkdir(parents=True)
        for entry in definition_entries(source):
            entry = Path(entry)
            if entry.is_dir():
                shutil.copytree(entry, staged / entry.name)
            else:
                shutil.copy2(entry, staged / entry.name)
        # The org's OWN provenance record (the packer-excluded file): owner = the org,
        # origin = installed — a copy, never the author's original.
        ownership_store.write(
            staged,
            ownership.OwnershipRecord(
                owner=org_id,
                origin=ownership.INSTALLED,
                source_id=agent_id,
                author=author,
            ),
        )
        shutil.rmtree(target, ignore_errors=True)  # replace = re-share of a newer build
        staged.rename(target)
    except OSError as e:
        shutil.rmtree(staged, ignore_errors=True)
        return f"install failed: {e}"
    return ""
