"""Engine catalogues — where a stub learns which engine to install, and how to verify it.

Three adapters and a chain, because there are genuinely three answers depending on who is building:

  ``ConfigEngineCatalog``    an explicit url + digest in config. The override, and the only thing
                             that works before an engine has ever been published.
  ``IndexEngineCatalog``     the registry's own ``engine`` block. The normal answer: whoever
                             releases the engine publishes it once and every later build follows.
  ``StaticEngineCatalog``    a fixed answer, for tests and for a service handed its engine ref
                             by its own configuration.

NOTHING IS HARDCODED HERE — no url, no version, no digest. A default would be the worst possible
kind: it would appear to work, produce stubs that download something from a host this build has
never heard of, and fail only on a stranger's machine.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agent_runtime.domain.bundle import BundleError, parse_registry_index
from agent_runtime.domain.product import EngineRef

log = logging.getLogger("agentd")


class StaticEngineCatalog:
    """A fixed set of refs, keyed by platform."""

    def __init__(self, *refs: EngineRef):
        self._by_platform = {r.platform: r for r in refs if r.platform}

    def resolve(self, platform: str) -> EngineRef | None:
        return self._by_platform.get((platform or "").strip().lower())


class ConfigEngineCatalog:
    """Reads ``engine_installer_url`` / ``engine_installer_sha256`` / ``engine_version`` off config.

    Answers for ONE platform — the one the operator configured, declared by
    ``engine_installer_platform`` (default "win", the only platform with a stub builder today). A
    config that names a url without saying what it is for would otherwise be silently offered to a
    mac build as if it applied.
    """

    def __init__(self, config):
        self._config = config

    def resolve(self, platform: str) -> EngineRef | None:
        want = (platform or "").strip().lower()
        declared = str(getattr(self._config, "engine_installer_platform", "") or "win").lower()
        if want != declared:
            return None
        url = str(getattr(self._config, "engine_installer_url", "") or "").strip()
        if not url:
            return None
        return EngineRef(
            platform=want,
            version=str(getattr(self._config, "engine_version", "") or "").strip(),
            url=url,
            sha256=str(getattr(self._config, "engine_installer_sha256", "") or "").strip().lower(),
        )


class IndexEngineCatalog:
    """Reads the ``engine`` block out of a registry index (a url or a local path).

    Relative engine urls are resolved against the index location, exactly like bundle and installer
    urls — so a registry directory is portable: publish it anywhere and every url still points
    inside it.

    A registry that cannot be read is NOT an error here. It returns None, the build service turns
    that into "no engine configured" with instructions, and the payload is still produced. A build
    that died because a CDN was slow would be a worse trade.
    """

    def __init__(self, index_url: str, timeout: float = 10.0, opener=None):
        # `registry_url` is allowed to be a directory, a file:// url or an http(s) url pointing at
        # either the index or its folder. That normalisation already exists for the DOWNLOAD side;
        # reusing it is what keeps "where is the registry?" from having two answers.
        from agent_runtime.infrastructure.marketplace.registry_client import normalize_registry_url

        raw = str(index_url or "").strip()
        self._index_url = normalize_registry_url(raw) if raw else ""
        self._timeout = timeout
        self._opener = opener or self._read

    def resolve(self, platform: str) -> EngineRef | None:
        if not self._index_url:
            return None
        try:
            raw = self._opener(self._index_url)
        except (OSError, urllib.error.URLError) as e:
            log.info("engine catalog: cannot read %s (%s)", self._index_url, e)
            return None
        try:
            index = parse_registry_index(json.loads(raw))
        except (ValueError, BundleError) as e:
            log.warning("engine catalog: %s is not a usable registry index (%s)", self._index_url, e)
            return None
        engine = index.engine_for(platform)
        if engine is None:
            return None
        return EngineRef(**{**engine.__dict__, "url": self._absolute(engine.url)})

    def _absolute(self, url: str) -> str:
        if "://" in url or not url:
            return url
        if "://" in self._index_url:
            return urllib.parse.urljoin(self._index_url, url)
        return str((Path(self._index_url).parent / url).resolve())

    def _read(self, location: str) -> str:
        if "://" in location:
            with urllib.request.urlopen(location, timeout=self._timeout) as response:  # noqa: S310
                return response.read().decode("utf-8")
        return Path(location).read_text(encoding="utf-8")


class ChainEngineCatalog:
    """First catalogue with a usable answer wins. Config before registry, so an operator can
    override a published engine without editing a registry they may not own."""

    def __init__(self, *catalogs):
        self._catalogs = [c for c in catalogs if c is not None]

    def resolve(self, platform: str) -> EngineRef | None:
        first_unusable: EngineRef | None = None
        for catalog in self._catalogs:
            ref = catalog.resolve(platform)
            if ref is None:
                continue
            if ref.usable:
                return ref
            # Remember it so the caller can report a HALF-configured engine (a url with no digest)
            # rather than the much more confusing "no engine at all".
            first_unusable = first_unusable or ref
        return first_unusable
