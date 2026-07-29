"""Plugin-sandbox seam (infrastructure side).

The trust boundary for UNTRUSTED plugin tools — tools that ride in inside a marketplace agent's own
package (``agents/<id>/plugins/``). Trusted, first-party tools are untouched and keep running
in-process.

  * ``LocalPluginSandbox``      — default backend: in-process passthrough (no isolation yet).
  * ``SandboxedTool``           — the transparent wrapper that routes execute() through a backend.
  * ``DefaultCapabilityResolver``— the conservative, non-interactive grant (approval layer later).
  * ``classify_origin`` / ``wrap_untrusted`` — the single classify+wrap decision point.

Ports live in ``agentd.application.interfaces.plugin_sandbox``; value objects in
``agentd.domain.sandbox``. Swap ``LocalPluginSandbox`` for a gVisor/Firecracker/remote backend
behind the same port without touching the daemon.
"""

from __future__ import annotations

from .capabilities import DefaultCapabilityResolver
from .classify import classify_origin, wrap_untrusted
from .local import LocalPluginSandbox
from .sandboxed_tool import SandboxedTool

__all__ = [
    "DefaultCapabilityResolver",
    "LocalPluginSandbox",
    "SandboxedTool",
    "classify_origin",
    "wrap_untrusted",
]
