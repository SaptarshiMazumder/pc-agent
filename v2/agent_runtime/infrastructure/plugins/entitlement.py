"""Entitlement policies — the DISTRIBUTION gate (architecture, not billing).

``AllowAllEntitlement`` is the default: every discovered, enabled, compatible plugin is entitled
(open-source behavior). ``LicenseEntitlement`` (M7) is the commercial policy: a plugin whose
manifest declares ``[distribution] entitlement = "<sku>"`` loads only when a VERIFIED license in
~/.agentd/licenses/ grants that SKU; unmarked plugins stay free. ``build_entitlement`` is the one
composition-root decision — neither the core nor any plugin knows how the decision is made.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")


class AllowAllEntitlement:
    """Entitle everything. The default policy; swapped for LicenseEntitlement when the
    distribution profile pins a publisher key (a commercial build)."""

    def is_entitled(self, manifest) -> bool:
        return True


class LicenseEntitlement:
    """SKU-gated entitlement: free plugins (no `entitlement` in the manifest) always
    pass; marked plugins need their SKU in the verified-license set."""

    def __init__(self, skus: set[str]):
        self._skus = set(skus)

    def is_entitled(self, manifest) -> bool:
        sku = getattr(manifest, "entitlement", "") or ""
        if not sku or sku in self._skus:
            return True
        log.info(
            "plugins: '%s' needs license SKU '%s' — not present "
            "(drop the .lic file into the licenses folder and restart)",
            getattr(manifest, "id", "?"),
            sku,
        )
        return False


def build_entitlement(config):
    """The composition root's ONE entitlement decision: a pinned publisher key in the
    distribution profile switches on license enforcement; otherwise everything is
    entitled (the open default). Licenses are read at build time (restart to re-read —
    the same cadence as plugin discovery)."""
    profile = getattr(config, "distribution", None)
    publisher_key = getattr(profile, "publisher_key", "") if profile else ""
    if not publisher_key:
        return AllowAllEntitlement()
    from agent_runtime import runtime_paths
    from agent_runtime.infrastructure.licensing import entitled_skus, load_licenses

    skus = entitled_skus(load_licenses(runtime_paths.licenses_dir(), publisher_key))
    log.info("entitlement: license mode (publisher key pinned) — %d SKU(s) licensed", len(skus))
    return LicenseEntitlement(skus)
