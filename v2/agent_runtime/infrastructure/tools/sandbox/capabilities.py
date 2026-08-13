"""DefaultCapabilityResolver — the conservative, NON-interactive grant used until approval exists.

It hands an untrusted tool the minimum a normal run needs and nothing more:
  * fs      -> only the current run's workspace (so plugin output lands where the agent expects it),
  * read    -> plus its OWN agent folder, read-only: the data it shipped with (templates, styles),
  * net     -> nothing,
  * secrets -> {} ALWAYS (default-deny; the sandbox stays blind to platform keys),
  * models  -> only if the tool DECLARES it needs one, and only ids it could legitimately resolve,
  * limits  -> wall-clock, and CPU/memory where the platform can enforce them.

This is the seam the approval/consent layer replaces later: an ApprovalCapabilityResolver will
implement the same `resolve()` by consulting user consent + a plugin's requested caps + policy.
Swapping it changes WHAT is granted; the sandbox that ENFORCES the grant does not change.

The limits come from `config.sandbox_limits` rather than being fixed here, because the right
ceiling is a property of the deployment: a hosted task sharing CPU across accounts wants a tight
one, and a desktop running a plugin that renders video for two minutes wants a loose one. The
defaults below are the loose end — a limit that breaks legitimate work gets switched off wholesale,
which costs more than it saves.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agent_runtime.application.run_context import RunContext
from agent_runtime.domain.sandbox import CapabilityGrant, PluginOrigin
from agent_runtime.domain.sandbox_net import resolve_allowlist

log = logging.getLogger("agentd")

DEFAULT_TIMEOUT_S = 120.0


class DefaultCapabilityResolver:
    name = "default"

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S, config=None) -> None:
        self._config = config
        limits = dict(getattr(config, "sandbox_limits", None) or {}) if config is not None else {}
        self._timeout_s = float(limits.get("timeout_s") or timeout_s)
        self._cpu_ms = int(limits.get("cpu_ms") or 0)
        self._mem_mb = int(limits.get("mem_mb") or 0)

    def resolve(
        self,
        plugin_id: str,
        origin: PluginOrigin,
        ctx: RunContext | None,
        tool: object = None,
    ) -> CapabilityGrant:
        workspace = ctx.workspace if (ctx is not None and ctx.workspace) else ""
        fs_paths = (workspace,) if workspace else ()
        # `secrets` STAYS EMPTY even for a plugin that declared some. Those are host-held: the
        # grant's secrets are injected into the child's ENVIRONMENT (see protocol.grant_payload),
        # and the whole point of declaring a credential by NAME is that the value never gets
        # there. The fetch broker substitutes it into the outbound request instead.
        return CapabilityGrant(
            fs_paths=fs_paths,
            read_paths=self._read_for(tool),
            timeout_s=self._timeout_s,
            cpu_ms=self._cpu_ms,
            mem_mb=self._mem_mb,
            models=self._models_for(plugin_id, tool),
            net_allowlist=self._net_for(tool),
        )

    # ------------------------------------------------------------------ own shipped files

    @staticmethod
    def _read_for(tool: object) -> tuple[str, ...]:
        """The tool's OWN agent folder, read-only — the data it shipped with.

        A plugin's templates/styles/reference files sit beside its code inside the agent dir,
        because that folder is what the .agentpkg carries and what an install unpacks; the
        workspace is a DIFFERENT tree (per-account run data), so it can never be where shipped
        data is found. Granting the folder the code already came from adds no reach a plugin
        did not already have — the code is right there — it only stops the sandbox denying a
        plugin its own package. Stamped at discovery (`_agent_dir`); the plugin root's parent
        covers a tool assembled without that tag."""
        agent_dir = str(getattr(tool, "_agent_dir", "") or "").strip()
        if not agent_dir:
            root = str(getattr(tool, "_plugin_root", "") or "").strip()
            if root:
                # <agent>/plugins/<plugin-id>/ -> <agent>
                agent_dir = str(Path(root).resolve().parents[1]) if len(Path(root).resolve().parents) > 1 else ""
        return (agent_dir,) if agent_dir else ()

    # ------------------------------------------------------------------ network

    def _net_for(self, tool: object) -> tuple[str, ...]:
        """Hosts the HOST will call for this tool. () unless its plugin.toml declared some.

        Read off the tool because discovery stamps the manifest's `[sandbox] net` there. The
        declaration is a ceiling: `resolve_allowlist` narrows it by operator config and cannot
        widen it, so what a user read in the package before installing stays the upper bound.
        """
        declared = getattr(tool, "_sandbox_net", ()) or ()
        if not declared:
            return ()
        return resolve_allowlist(
            declared,
            getattr(self._config, "sandbox_net_allow", ()) if self._config is not None else (),
            getattr(self._config, "sandbox_net_deny", ()) if self._config is not None else (),
        )

    # ------------------------------------------------------------------ models

    def _models_for(self, plugin_id: str, tool: object) -> tuple[str, ...]:
        """Which models this tool may ask the host to run. () unless it declares it needs one.

        The declaration is `Tool.needs_model`, which every model-bearing tool already sets so the
        catalog can offer a model picker. Reusing it means there is ONE place a tool says "I call a
        model" — a second declaration in plugin.toml could disagree with the first, and then the
        interesting question becomes which one the sandbox believes.
        """
        if self._config is None or not getattr(tool, "needs_model", False):
            return ()
        # An operator can pin the set outright; otherwise it is DERIVED from what this tool would
        # have resolved to anyway, so the sandbox neither widens nor narrows the normal choice.
        pinned = tuple(str(m).strip() for m in (getattr(self._config, "sandbox_models", ()) or ()) if str(m).strip())
        if pinned:
            return pinned
        return self._resolvable_models(plugin_id, tool)

    def _resolvable_models(self, plugin_id: str, tool: object) -> tuple[str, ...]:
        """The ids this tool could legitimately end up asking for, via the normal resolution chain.

        Every step of that chain is included, not just its first hit: the tool re-resolves its id
        INSIDE the sandbox against a projected config, and if that projection ever lands one step
        further down the chain than the host would, a grant holding only the host's answer would
        refuse a completely legitimate call. These are all the same tool's own configured model —
        including them all is not a meaningful widening, and omitting one is a mystery failure.

        The brain is in the list for text tools because that is the documented rule: a text tool
        with nothing configured inherits the brain, which is exactly what a plugin's
        ``resolve_model(...) or brain_model(...)`` does.
        """
        from agent_runtime.application.tool_models import (
            brain_model,
            kind_default_model,
            resolve_tool_model,
        )

        kind = str(getattr(tool, "model_kind", "") or "text")
        produces = [
            lambda: resolve_tool_model(
                self._config,
                str(getattr(tool, "plugin", "") or plugin_id),
                str(getattr(tool, "name", "") or ""),
                default=str(getattr(tool, "default_model", "") or "") or None,
                kind=kind,
            ),
            lambda: kind_default_model(self._config, kind),
        ]
        if kind == "text":
            produces.append(lambda: brain_model(self._config))
        candidates: list[str] = []
        for produce in produces:
            try:
                value = produce()
            except Exception as e:  # noqa: BLE001 — an unresolvable model is a denial, not a crash
                log.debug("sandbox: model resolution for '%s' failed: %s", plugin_id, e)
                continue
            if value:
                candidates.append(str(value))
        if not candidates:
            log.info(
                "sandbox: '%s' declares needs_model but no model resolves — granting none",
                getattr(tool, "name", plugin_id),
            )
        return tuple(dict.fromkeys(candidates))
