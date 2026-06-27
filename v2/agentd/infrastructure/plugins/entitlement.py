"""Entitlement policies — the DISTRIBUTION gate (architecture, not billing).

``AllowAllEntitlement`` is the default: every discovered, enabled, compatible plugin is entitled
(open-source behavior). A commercial deployment provides its own ``EntitlementPolicy`` (see
agentd.application.interfaces.plugins) that consults the tenant's plan/licence to gate optional or
paid bundles — injected at the composition root, so neither the core nor any plugin knows how the
decision is made.
"""

from __future__ import annotations


class AllowAllEntitlement:
    """Entitle everything. The default policy; swap it out in a commercial build."""

    def is_entitled(self, manifest) -> bool:
        return True
