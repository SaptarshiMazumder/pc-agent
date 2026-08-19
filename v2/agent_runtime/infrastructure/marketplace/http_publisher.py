"""HttpRegistryPublisher — the AUTHOR path. Publish without holding the marketplace's key.

    POST <publish service>/registry/publish     multipart: the .agentpkg (+ its installer)

WHAT MAKES THIS THE POINT OF THE WHOLE FEATURE. The operator path needs the registry's ed25519
private key and AWS write credentials on the publisher's machine. Neither can be given to an
ordinary author, so until this existed "users can publish to the marketplace" was false by
construction, no matter how good the tooling around it was. Here the author sends a package and
their ordinary session token; the service resolves that to a creator and signs with THAT creator's
key, server-side. The root key stays offline and the author never sees a key at all.

The trust model this rides on already exists (``marketplace/trust.py``, schema 2): clients pin only
the platform ROOT key, which signs a ROSTER of creators; each creator's own key signs their own
bundles. So a service can verify a submission against a key the roster ALREADY lists and never
needs the root private key. That is exactly what makes publishing safe to run as a web service.

TWO OUTCOMES THAT ARE NOT ERRORS, and conflating either with failure sends authors hunting for
bugs in their agent:
  * 202 — accepted, awaiting review. A creator's FIRST publish files for admission to the roster.
  * a dry run — nothing was sent at all.

NO NEW DEPENDENCY: httpx is already how this codebase talks to the accounts service.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from agent_runtime.application.interfaces.bundle_publisher import PublishRequest, PublishResult

log = logging.getLogger("agentd")

PUBLISH_PATH = "/registry/publish"


def platform_session_token(config=None) -> str:
    """The author's credential — WHO they are, not whether they are paying us.

    ONE identity, two doors to the same value:

      1. THE CONNECTION — the account bound to the current socket (`?session=` at dial time,
         resolved by the accounts service, pinned on a contextvar). This is where every UI-driven
         publish gets its identity, desktop and hosted alike; per-connection because a
         multi-tenant daemon serves many people at once and publishing as the wrong one would be
         the worst possible bug in this file.
      2. AGENTD_SESSION_TOKEN — the same session token, exported by hand. For the paths that have
         no connection at all: the offline CLI (`agentd bundle roster …`) and CI.

    WHAT IS DELIBERATELY NOT HERE: the model-proxy key (AGENTD_MODEL_PROXY_KEY and its config
    twin). That is a BILLING credential — who pays for inference — and treating it as identity is
    the conflation this function used to embody: an author on their own API keys was refused with
    "you are not signed in" (false), and a machine key could publish as nobody in particular.
    Sign-in and payment are separate locks; this reads only the identity one.

    (`config` is accepted and ignored — the parameter predates the single-identity rule.)
    """
    try:
        from agent_runtime.infrastructure import accounts

        current = accounts.current_account.get()
        if current and current.get("session_token"):
            return str(current["session_token"])
    except Exception:  # noqa: BLE001 — accounts is optional; fall through to the ambient identity
        pass
    return (os.environ.get("AGENTD_SESSION_TOKEN") or "").strip()


class HttpRegistryPublisher:
    """:param packer: an ``AgentPacker`` — the SAME packing the operator path uses.
    :param stub_provider: optional ``(agent_dir) -> Path | None`` that builds the per-agent
        installer. Injected rather than built here: producing a product is not this class's job,
        and a host with no NSIS must still be able to publish the bundle."""

    def __init__(self, service_url: str, config, packer, stub_provider=None, timeout: float = 300.0):
        self._url = (service_url or "").strip().rstrip("/")
        self._config = config
        self._packer = packer
        self._stub_provider = stub_provider
        self._timeout = timeout

    @property
    def name(self) -> str:
        return f"the publish service at {self._url}" if self._url else "(no publish service)"

    def requirements(self) -> list[str]:
        missing = []
        if not self._url:
            missing.append(
                'publish_target — the publish service, e.g. "https://api.example.com" '
                "(env: AGENTD_PUBLISH_TARGET)"
            )
        if not platform_session_token(self._config):
            missing.append(
                "you are not signed in. Publishing identifies you as the creator, so sign in "
                "first and try again. Signing in is enough on its own — it does not require "
                "Cloud mode, and your own API keys can keep paying for model calls. NOTE: no "
                "signing key is needed — the service signs with your creator key."
            )
        return missing

    # ------------------------------------------------------------------ publish
    def publish(self, request: PublishRequest) -> PublishResult:
        import tempfile

        agent_dir = Path(request.agent_dir)
        with tempfile.TemporaryDirectory(prefix="agentd-publish-") as work:
            staging = Path(work)
            try:
                package = self._packer.pack(agent_dir, staging, request.version)
            except ValueError as e:
                return PublishResult(ok=False, message=str(e))

            warnings: list[str] = []
            installer: Path | None = None
            if request.with_installer:
                installer, reason = self._build_installer(agent_dir)
                if reason:
                    warnings.append(reason)

            from agent_runtime.infrastructure.marketplace import bundle_io

            manifest = bundle_io.read_manifest(package)
            if request.dry_run:
                return PublishResult(
                    ok=True,
                    dry_run=True,
                    bundle_id=manifest.id,
                    version=manifest.version,
                    message=f"PREVIEW — nothing sent. Would publish to {self.name}.",
                    detail=self._preview(manifest, package, installer),
                    warnings=warnings,
                )
            result = self._post(manifest, package, installer)
            result.warnings = warnings + result.warnings
            return result

    # ------------------------------------------------------------------ internals
    def _build_installer(self, agent_dir: Path) -> tuple[Path | None, str]:
        """-> (the stub, "") or (None, why not). Never raises: no installer is a degraded publish,
        not a failed one — the bundle still reaches everyone who already has the engine."""
        if self._stub_provider is None:
            return None, (
                "no per-agent installer was published: this install cannot build one. The bundle "
                "is listed for people who already have agentd; a stranger with a bare machine will "
                "have nothing to download."
            )
        try:
            stub = self._stub_provider(agent_dir)
        except Exception as e:  # noqa: BLE001 — a build failure must not lose the publish
            return None, f"no per-agent installer was published (building it failed: {e})"
        if stub is None:
            return None, (
                "no per-agent installer was published — see the build warnings above for why."
            )
        return Path(stub), ""

    def _preview(self, manifest, package: Path, installer: Path | None) -> str:
        lines = [
            f"POST {self._url}{PUBLISH_PATH}",
            f"  bundle    {manifest.id} {manifest.version}",
            f"  package   {package.name}  ({package.stat().st_size:,} bytes)",
        ]
        lines.append(
            f"  installer {installer.name}  ({installer.stat().st_size:,} bytes)"
            if installer
            else "  installer (none)"
        )
        lines += [
            "",
            "The service will: authenticate you, resolve your creator id, check the version is",
            "newer than any already published, confirm the bundle id is yours, sign the entry with",
            "your creator key, and append it to the index under a lock.",
        ]
        return "\n".join(lines)

    def _post(self, manifest, package: Path, installer: Path | None) -> PublishResult:
        import httpx

        token = platform_session_token(self._config)
        files = {"package": (package.name, package.read_bytes(), "application/octet-stream")}
        if installer is not None:
            files["installer"] = (
                installer.name,
                installer.read_bytes(),
                "application/octet-stream",
            )
        data = {"bundle_id": manifest.id, "version": manifest.version}
        try:
            response = httpx.post(
                f"{self._url}{PUBLISH_PATH}",
                headers={"Authorization": f"Bearer {token}"},
                files=files,
                data=data,
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            # A network failure is a RESULT. The registry is untouched, and telling the author
            # "could not reach the service" is actionable in a way a traceback is not.
            return PublishResult(
                ok=False,
                bundle_id=manifest.id,
                version=manifest.version,
                message=f"could not reach {self.name}: {e}. Nothing was published.",
            )

        body = self._json(response)
        message = str(body.get("message") or "").strip()
        if response.status_code == 202:
            return PublishResult(
                ok=True,
                pending=True,
                bundle_id=manifest.id,
                version=manifest.version,
                message=message
                or (
                    "accepted, awaiting review. Your first publish admits you to the creator "
                    "roster; once an operator approves it, this and every later publish list "
                    "automatically."
                ),
                detail=json.dumps(body, indent=2) if body else response.text,
            )
        if response.status_code in (200, 201):
            return PublishResult(
                ok=True,
                bundle_id=str(body.get("bundle_id") or manifest.id),
                version=str(body.get("version") or manifest.version),
                url=str(body.get("url") or ""),
                installer_url=str(body.get("installer_url") or ""),
                message=message or f"published {manifest.id} {manifest.version}",
                detail=json.dumps(body, indent=2) if body else response.text,
            )
        return PublishResult(
            ok=False,
            bundle_id=manifest.id,
            version=manifest.version,
            message=message or self._explain(response.status_code),
            detail=response.text[:4000],
        )

    @staticmethod
    def _json(response) -> dict:
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _explain(status: int) -> str:
        """A status code the author can act on. The service normally sends its own message; this is
        the fallback, and a bare '409' is not something anyone can do anything about."""
        return {
            401: "not signed in, or the session expired. Sign in again and retry.",
            403: "this account may not publish (revoked, or not a creator).",
            409: (
                "that bundle id belongs to another creator, or this version is not newer than "
                "the published one. Publishing again raises the number by default; if you asked "
                "to keep it, publish with a higher version instead — installs supersede BY "
                "VERSION, so republishing the same number reaches nobody."
            ),
            413: "the package is too large for the publish service.",
            429: "too many publishes; wait and retry.",
        }.get(status, f"the publish service refused this (HTTP {status}).")
