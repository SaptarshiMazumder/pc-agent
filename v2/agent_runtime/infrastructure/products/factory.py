"""build_product_service — the COMPOSITION ROOT for product building.

One place that knows which concrete adapter goes where, so the three callers (`agentd product`,
Agent Builder's publish tool, the publish service) all get an identically wired service instead of
each assembling their own and drifting.

TWO THINGS IT DECIDES, and both are lookups rather than constants:

  the stub builder   chosen by TARGET platform, not by the host. makensis cross-compiles, so a
                     Linux service builds a Windows installer with the same adapter an author on
                     Windows uses. A target with no builder yields None, and the service reports
                     "payload only" instead of failing.
  the engine         config first (an operator override), then the registry index this install is
                     already pointed at. Nothing is hardcoded: no url, no digest, no version.
"""

from __future__ import annotations

from agent_runtime.application.services.build_product_service import BuildProductService
from agent_runtime.domain.product import PlatformEndpoints, ProductDefaults, ProductRules
from agent_runtime.infrastructure.products.agent_packer import BundleAgentPacker
from agent_runtime.infrastructure.products.engine_catalog import (
    ChainEngineCatalog,
    ConfigEngineCatalog,
    IndexEngineCatalog,
)
from agent_runtime.infrastructure.products.nsis_stub_builder import NsisStubBuilder
from agent_runtime.infrastructure.products.payload_writer import FsPayloadWriter
from agent_runtime.infrastructure.products.source_reader import FsProductSourceReader

# target platform -> the builder that produces its installer. A dict, so adding a mac builder is
# one entry and no branch: `agentd product build --platform mac` starts working the day it exists.
STUB_BUILDERS = {NsisStubBuilder.platform: NsisStubBuilder}

DEFAULT_TARGET = NsisStubBuilder.platform


def product_defaults(config) -> ProductDefaults:
    """Install-wide defaults for what an agent cannot say about itself.

    The platform endpoints come from THIS install's distribution profile, which is the correct
    source: a product built by a hosted build signs into the same backend that build signs into,
    and a BYOK checkout produces BYOK products. Hardcoding either would ship someone else's
    accounts url inside another author's installer.
    """
    profile = getattr(config, "distribution", None)
    endpoints = PlatformEndpoints(
        accounts_url=str(getattr(profile, "accounts_url", "") or ""),
        model_proxy_url=str(getattr(profile, "model_proxy_url", "") or ""),
    )
    return ProductDefaults(
        platform=endpoints,
        engine_min_version=str(getattr(config, "engine_min_version", "") or ""),
    )


def engine_catalog(config):
    """Config override, then the registry index. First usable answer wins."""
    return ChainEngineCatalog(
        ConfigEngineCatalog(config),
        IndexEngineCatalog(str(getattr(config, "registry_url", "") or "")),
    )


def build_product_service(config, target: str = DEFAULT_TARGET) -> BuildProductService:
    reader = FsProductSourceReader()
    builder_class = STUB_BUILDERS.get((target or DEFAULT_TARGET).strip().lower())
    stub_builder = None
    if builder_class is not None:
        stub_builder = builder_class(makensis=str(getattr(config, "makensis_path", "") or ""))
    return BuildProductService(
        rules=ProductRules(product_defaults(config)),
        reader=reader,
        payload_writer=FsPayloadWriter(BundleAgentPacker(), reader),
        stub_builder=stub_builder,
        engine_catalog=engine_catalog(config),
    )
