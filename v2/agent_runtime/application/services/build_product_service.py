"""BuildProductService — derive a product, write its payload, build its stub. In that order.

The ONLY place that order lives. It used to be spread across two Node scripts and a bash script
that called them (`gen-app-flavor.mjs` -> `dist-app.mjs`, orchestrated by
`build-agent-installer.sh`), which is why "the version came out wrong" was three bugs instead of
one. Everything below is ports, so this same object serves an author's shell, Agent Builder's
publish tool, and a publish service on Linux.

REFUSES BEFORE IT WRITES. The spec is derived first, and deriving is what rejects an agent with no
[app] section. A refusal after the payload directory exists leaves a half-built product on disk
that looks buildable.

THE PAYLOAD IS WORTH HAVING WITHOUT THE STUB. Two of the three callers cannot build an installer:
a Linux service has no NSIS, and neither does a plain checkout. So a missing stub builder, a
missing NSIS, or an install with no engine configured are all reported as WARNINGS on a successful
payload — never as a failed build. What is never allowed is a stub that cannot work: if the engine
is unknown or unverifiable, no installer is produced at all, because an installer that downloads
nothing and shortcuts to a program that was never installed is worse than no installer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_runtime.application.interfaces.product import (
    EngineRef,
    PayloadManifest,
    ProductSource,
)
from agent_runtime.domain.product import ProductOverrides, ProductRules, ProductSpec


@dataclass
class ProductBuild:
    """What a build produced, and everything it could not produce and why."""

    spec: ProductSpec
    payload: PayloadManifest
    stub: Path | None = None
    engine: EngineRef | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True when a stranger with no agentd could install this — i.e. there is an installer."""
        return self.stub is not None


class BuildProductService:
    def __init__(
        self,
        rules: ProductRules,
        reader,
        payload_writer,
        stub_builder=None,
        engine_catalog=None,
    ):
        """:param stub_builder: None on a platform/host that cannot build installers.
        :param engine_catalog: None when this install knows of no published engine."""
        self._rules = rules
        self._reader = reader
        self._payload_writer = payload_writer
        self._stub_builder = stub_builder
        self._engine_catalog = engine_catalog

    # ------------------------------------------------------------------ derive
    def derive(self, source: ProductSource, overrides: ProductOverrides | None = None) -> ProductSpec:
        """The refusal step, on its own, so a caller can validate without producing anything."""
        agent_id, agent_toml = self._reader.declaration(source)
        return self._rules.derive(agent_id, agent_toml, overrides)

    # ------------------------------------------------------------------ payload
    def payload(
        self,
        source: ProductSource,
        payload_dir: Path,
        overrides: ProductOverrides | None = None,
    ) -> ProductBuild:
        """Everything except the installer. This is the part that carries the agent."""
        spec = self.derive(source, overrides)
        manifest = self._payload_writer.write(spec, source, Path(payload_dir))
        return ProductBuild(spec=spec, payload=manifest)

    # ------------------------------------------------------------------ full build
    def build(
        self,
        source: ProductSource,
        payload_dir: Path,
        stub_dir: Path | None = None,
        overrides: ProductOverrides | None = None,
    ) -> ProductBuild:
        """Payload, then installer. Never raises for a missing toolchain — see the module docstring."""
        build = self.payload(source, payload_dir, overrides)

        if self._stub_builder is None:
            build.warnings.append(
                "no installer was built: this host has no stub builder for its platform. The "
                "payload is complete — an existing engine can run it with --app-dir."
            )
            return build

        reason = self._stub_builder.available()
        if reason:
            build.warnings.append(f"no installer was built: {reason}")
            return build

        engine = self._resolve_engine(build)
        if engine is None:
            return build
        build.engine = engine

        target_dir = Path(stub_dir) if stub_dir is not None else Path(payload_dir).parent
        target_dir.mkdir(parents=True, exist_ok=True)
        out_path = target_dir / build.spec.installer_filename(self._stub_builder.suffix)
        build.stub = self._stub_builder.build(build.spec, build.payload, engine, out_path)
        return build

    # ------------------------------------------------------------------ helpers
    def _resolve_engine(self, build: ProductBuild) -> EngineRef | None:
        """The engine, or None with the reason recorded as a warning.

        Both failure modes get a message naming what to set, because "no installer" with no
        explanation is indistinguishable from a broken build.
        """
        platform = self._stub_builder.platform
        engine = self._engine_catalog.resolve(platform) if self._engine_catalog else None
        if engine is None:
            build.warnings.append(
                f"no installer was built: this install knows of no {platform} engine to point a "
                "stub at. Set engine_installer_url + engine_installer_sha256 in config (or publish "
                "an engine to the registry) and build again."
            )
            return None
        if not engine.usable:
            build.warnings.append(
                f"no installer was built: the {platform} engine entry is incomplete "
                f"(url={'set' if engine.url else 'missing'}, "
                f"sha256={'set' if engine.sha256 else 'missing'}). A stub must be able to verify "
                "what it downloads before running it, so none was produced."
            )
            return None
        return engine
