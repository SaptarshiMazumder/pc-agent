"""Plugin-sandbox seam (infrastructure side).

The trust boundary for UNTRUSTED plugin tools — tools that ride in inside a marketplace agent's own
package (``agents/<id>/plugins/``). Trusted, first-party tools are untouched and keep running
in-process.

  * ``LocalPluginSandbox``      — in-process passthrough (no isolation). The desktop default.
  * ``SubprocessPluginSandbox`` — one child process per tool call: no runtime handles, scrubbed
                                  env, redacted config, audit-hook enforcement. The hosted default.
  * ``SandboxModelBroker``      — serves a sandboxed tool's model calls from the HOST, so the tool
                                  needs neither network nor a credential to use a model.
  * ``build_plugin_sandbox``    — the ONE place a backend is chosen (config-driven).
  * ``SandboxedTool``           — the transparent wrapper that routes execute() through a backend.
  * ``DefaultCapabilityResolver``— the conservative, non-interactive grant (approval layer later).
  * ``classify_origin`` / ``wrap_untrusted`` — the single classify+wrap decision point.

Ports live in ``agent_runtime.application.interfaces.plugin_sandbox``; value objects in
``agent_runtime.domain.sandbox``. A gVisor/Firecracker/remote backend joins them in ``backends.py``
behind the same port, without touching the daemon.
"""

from __future__ import annotations

from .backends import build_plugin_sandbox, resolve_backend_name
from .capabilities import DefaultCapabilityResolver
from .classify import classify_origin, wrap_untrusted
from .local import LocalPluginSandbox
from .model_broker import SandboxModelBroker
from .sandboxed_tool import SandboxedTool
from .subprocess_backend import SubprocessPluginSandbox

__all__ = [
    "DefaultCapabilityResolver",
    "LocalPluginSandbox",
    "SandboxModelBroker",
    "SandboxedTool",
    "SubprocessPluginSandbox",
    "build_plugin_sandbox",
    "classify_origin",
    "resolve_backend_name",
    "wrap_untrusted",
]
