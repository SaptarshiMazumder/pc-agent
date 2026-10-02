"""HttpRegistryPublisher — the AUTHOR path. Publish without holding the marketplace's key.

    POST <publish service>/registry/publish/upload   -> a size-bounded S3 upload slot
    POST <slot url>                                   the .agentpkg, straight to S3
    POST <publish service>/registry/publish          {upload_key, bundle_id, filename}

The per-agent installer is NOT built or sent from here: the service compiles its own from its own
stub template (whoever signs must compile), so an installer built on this machine was discarded on
arrival.

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
UPLOAD_PATH = "/registry/publish/upload"


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

        token = accounts.session_token()
        if token:
            return token
    except Exception:  # noqa: BLE001 — accounts is optional; fall through to the ambient identity
        pass
    return (os.environ.get("AGENTD_SESSION_TOKEN") or "").strip()


class HttpRegistryPublisher:
    """:param packer: an ``AgentPacker`` — the SAME packing the operator path uses."""

    def __init__(self, service_url: str, config, packer, timeout: float = 300.0):
        self._url = (service_url or "").strip().rstrip("/")
        self._config = config
        self._packer = packer
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

            from agent_runtime.infrastructure.marketplace import bundle_io

            manifest = bundle_io.read_manifest(package)
            if request.dry_run:
                return PublishResult(
                    ok=True,
                    dry_run=True,
                    bundle_id=manifest.id,
                    version=manifest.version,
                    message=f"PREVIEW — nothing sent. Would publish to {self.name}.",
                    detail=self._preview(manifest, package),
                )
            return self._send(manifest, package)

    # ------------------------------------------------------------------ internals
    def _preview(self, manifest, package: Path) -> str:
        return "\n".join(
            [
                f"POST {self._url}{PUBLISH_PATH}",
                f"  bundle    {manifest.id} {manifest.version}",
                f"  package   {package.name}  ({package.stat().st_size:,} bytes)",
                "",
                "The service will: authenticate you, resolve your creator id, check the version is",
                "newer than any already published, confirm the bundle id is yours, build the",
                "per-agent installer, sign the entry with your creator key, and append it to the",
                "index under a lock.",
            ]
        )

    def _send(self, manifest, package: Path) -> PublishResult:
        """Three requests, because the package does not travel through the publish service.

        The service sits behind a load balancer that caps a request body at 1 MB — an agent with a
        built window is past that before anything else is counted. So the service hands out a
        short-lived, size-bounded S3 upload slot, the package goes straight to S3, and the publish
        call names the slot instead of carrying the bytes:

            1. POST <service>/registry/publish/upload   -> {upload: {url, fields, key}}
            2. POST <upload.url>  (S3 form upload)       the .agentpkg
            3. POST <service>/registry/publish          {upload_key, bundle_id, filename}
        """
        import httpx

        token = platform_session_token(self._config)
        auth = {"Authorization": f"Bearer {token}"}
        try:
            slot_response = httpx.post(
                f"{self._url}{UPLOAD_PATH}",
                headers=auth,
                json={"bundle_id": manifest.id, "filename": package.name},
                timeout=self._timeout,
            )
            if slot_response.status_code != 200:
                return self._result(slot_response, manifest)
            slot = self._json(slot_response).get("upload") or {}
            if not slot.get("url") or not slot.get("key"):
                return self._refused(
                    manifest,
                    f"{self.name} answered the upload request without an upload slot: "
                    f"{slot_response.text[:500]}. Nothing was published.",
                )
            stored = httpx.post(
                str(slot["url"]),
                data=dict(slot.get("fields") or {}),
                files={"file": (package.name, package.read_bytes(), "application/octet-stream")},
                timeout=self._timeout,
            )
            if stored.status_code not in (200, 201, 204):
                return self._refused(
                    manifest,
                    f"uploading the package to storage failed (HTTP {stored.status_code}): "
                    f"{stored.text[:500]}. Nothing was published.",
                )
            response = httpx.post(
                f"{self._url}{PUBLISH_PATH}",
                headers=auth,
                json={
                    "upload_key": str(slot["key"]),
                    "bundle_id": manifest.id,
                    "filename": package.name,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            # A network failure is a RESULT. The registry is untouched, and telling the author
            # "could not reach the service" is actionable in a way a traceback is not.
            return self._refused(
                manifest, f"could not reach {self.name}: {e}. Nothing was published."
            )
        return self._result(response, manifest)

    @staticmethod
    def _refused(manifest, message: str) -> PublishResult:
        return PublishResult(
            ok=False, bundle_id=manifest.id, version=manifest.version, message=message
        )

    def _result(self, response, manifest) -> PublishResult:
        """The service's answer, as the author reads it. Its warnings (no installer built, …) are
        part of the answer, not detail — they say what a stranger will be missing."""
        body = self._json(response)
        message = str(body.get("message") or "").strip()
        warnings = [str(w) for w in (body.get("warnings") or []) if str(w).strip()]
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
                warnings=warnings,
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
                warnings=warnings,
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
