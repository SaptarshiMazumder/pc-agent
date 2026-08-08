"""Plugin-sandbox value objects — pure domain types (no I/O, no imports of the app/infra layers).

Two things live here, both plain data:

  * ``PluginOrigin`` — WHERE a tool's code came from, hence how much we trust it. The classifier
    stamps this so one decision point ("is this untrusted?") can be made from a value, not from
    scattered checks.
  * ``CapabilityGrant`` — WHAT an untrusted tool is allowed to touch when it runs (files, network,
    CPU/memory/time, secrets). DEFAULT-DENY: a bare ``CapabilityGrant()`` grants nothing and, above
    all, ``secrets = {}`` — a sandboxed tool NEVER sees provider keys or another user's tokens
    unless a grant explicitly hands them over.

The grant is *produced* by a ``CapabilityResolver`` (today a conservative default; later the
approval/consent layer) and *enforced* by a ``PluginSandbox`` backend. It only ever flows INTO the
sandbox — the sandbox enforces a grant, it never decides one. That split is what lets the approval
layer be built later without touching sandbox execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class PluginOrigin(str, Enum):
    """Trust provenance of the code behind a tool.

    BUILTIN / FIRST_PARTY  -> our code (the shipped standard library, or plugins the operator
                              installed). Runs in-process, unchanged, fast. NOT sandboxed.
    THIRD_PARTY_BUNDLE     -> code that rode in inside someone else's agent package (a marketplace
                              .agentpkg's agents/<id>/plugins/). UNTRUSTED -> must run behind a
                              PluginSandbox so it can't reach the host FS, network, secrets, or
                              other users' data.
    """

    BUILTIN = "builtin"
    FIRST_PARTY = "first_party"
    THIRD_PARTY_BUNDLE = "third_party_bundle"

    @property
    def is_untrusted(self) -> bool:
        return self is PluginOrigin.THIRD_PARTY_BUNDLE


@dataclass(frozen=True)
class CapabilityGrant:
    """The rights handed to ONE untrusted tool invocation. Default-deny in every field.

    ``fs_paths``      absolute paths the tool may read/write (e.g. just the caller's workspace).
    ``net_allowlist`` host[:port] / host globs the tool may reach ("" list = no network).
    ``cpu_ms``        CPU time ceiling in ms  (0 = backend default).
    ``mem_mb``        memory ceiling in MB    (0 = backend default).
    ``timeout_s``     wall-clock ceiling      (0 = backend default).
    ``secrets``       env/secret values injected INTO the sandbox. {} by default — the sandbox is
                      blind to platform keys and other users' tokens unless a grant opts specific
                      values in. This is the single most important default in the whole design.
    ``models``        model ids this tool may ask the host to run on its behalf. () = none.

    ON ``models`` BEING A CAPABILITY RATHER THAN A KEY. A sandboxed tool never receives a provider
    credential and never opens a socket; it asks the HOST to make the call (see the sandbox model
    broker). So the right to use a model is expressed the same way as the right to read a path — a
    list on the grant, default empty — instead of by handing over something the tool then owns.
    The list is also the CLAMP: naming an expensive model would otherwise be a cost attack, so the
    broker refuses anything not on it.
    """

    fs_paths: tuple[str, ...] = ()
    net_allowlist: tuple[str, ...] = ()
    cpu_ms: int = 0
    mem_mb: int = 0
    timeout_s: float = 0.0
    secrets: Mapping[str, str] = field(default_factory=dict)
    models: tuple[str, ...] = ()


# The deny-all grant: nothing readable, nothing reachable, no secrets. The safe fallback anywhere a
# resolver is missing or errors — never fabricate rights on failure.
DENY_ALL = CapabilityGrant()
