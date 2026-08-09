"""PublishIntakeService — an author's upload becomes a signed, listed, downloadable product.

THE ORDER IS THE DESIGN. Every step either refuses cheaply or is a step that cannot be undone, and
they are sequenced so the expensive and irreversible ones happen last:

     1. authenticate           unknown token -> 401, nothing read
     2. resolve the creator    not admitted yet -> 202 pending, nothing built
     3. read the manifest      not a usable .agentpkg -> 400
     4. own the bundle id      someone else's id -> 409  (the whole squatting surface)
     5. version is newer       a re-publish of the same number -> 409, said plainly
     6. BUILD                  payload + the per-agent installer, from OUR stub template
     7. SIGN                   the bundle digest and the installer digest, with the CREATOR's key
     8. artifacts, then index  under a lock, index LAST

WHY 8 IS WORTH THE CARE. An index is the registry's entire contents, and the two failure modes are
both silent:

  * write the index before the artifacts and there is a window where the store advertises a
    download that 404s;
  * read-modify-write without a lock and two simultaneous publishes each drop the other's entry —
    an author's agent vanishes from the marketplace with no error anywhere.

WHY THE SERVICE BUILDS THE INSTALLER instead of accepting one. Whoever signs must compile: an NSIS
installer runs arbitrary code at install time, unsandboxed, and signing a binary an author compiled
would put the platform's certificate on code it cannot inspect. Verifying an author's binary means
rebuilding it and comparing — at which point their build was pointless. So the author uploads a
~43 KB package and the service compiles from its own template.

NO INSTALLER IS A DEGRADED PUBLISH, NOT A FAILED ONE. If the toolchain or the engine reference is
missing, the bundle is still published and the response says what a stranger will be missing. The
alternative — refusing — would take the marketplace down for an operator-side problem.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agent_runtime.application.interfaces.publish_intake import (
    BAD_REQUEST,
    CONFLICT,
    FORBIDDEN,
    OK,
    PENDING,
    TOO_LARGE,
    UNAUTHORIZED,
    IntakeResult,
    Submission,
)
from agent_runtime.domain.bundle import BundleError, is_update

log = logging.getLogger("agentd")

# A generous ceiling that still bounds a single request's cost. An agent is text, skills and small
# assets; anything approaching this is a mistake or an attack, and either way the answer is the same.
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


class PublishIntakeService:
    def __init__(
        self,
        authenticator,
        creators,
        signer,
        index_store,
        lock,
        product_service=None,
        max_bytes: int = MAX_PACKAGE_BYTES,
    ):
        """:param product_service: a BuildProductService. None => publish bundles only, no
        installers, reported as a warning on every publish rather than silently."""
        self._auth = authenticator
        self._creators = creators
        self._signer = signer
        self._store = index_store
        self._lock = lock
        self._products = product_service
        self._max_bytes = max_bytes

    # ================================================================== entry point
    def submit(self, submission: Submission) -> IntakeResult:
        if not submission.package:
            return IntakeResult(BAD_REQUEST, "no package was uploaded.")
        if len(submission.package) > self._max_bytes:
            return IntakeResult(
                TOO_LARGE,
                f"that package is {len(submission.package):,} bytes; the limit is "
                f"{self._max_bytes:,}.",
            )

        account = self._auth.account(submission.token)
        if not account:
            return IntakeResult(
                UNAUTHORIZED, "not signed in, or the session expired. Sign in again and retry."
            )
        account_id = str(account.get("account_id") or account.get("id") or "").strip()
        if not account_id:
            return IntakeResult(UNAUTHORIZED, "that session has no account.")

        creator = self._creators.for_account(
            account_id, str(account.get("name") or account.get("email") or "")
        )
        if creator.state == "revoked":
            return IntakeResult(FORBIDDEN, "this account may not publish.", creator_id=creator.id)
        if not creator.may_publish:
            # NOT an error. The roster is signed by an offline root key, so admitting a creator is
            # an operator action by construction. Saying "failed" here sends authors looking for a
            # bug in an agent that is fine.
            return IntakeResult(
                PENDING,
                "accepted, awaiting review. Your first publish admits you to the creator roster; "
                "once an operator approves it, this and every later publish list automatically. "
                "You do not need to publish again.",
                creator_id=creator.id,
            )

        import tempfile

        with tempfile.TemporaryDirectory(prefix="agentd-intake-") as work:
            return self._publish(submission, creator, Path(work))

    # ================================================================== the real work
    def _publish(self, submission: Submission, creator, work: Path) -> IntakeResult:
        from agent_runtime.infrastructure.marketplace import bundle_io

        # The uploaded bytes become a file because everything downstream — manifest reading, the
        # payload writer, makensis — is path-based, and reusing those exact code paths is what
        # keeps the service's output identical to `agentd bundle publish`'s.
        # A NEUTRAL name, always. The client's filename never names a file here: it is
        # attacker-controlled, it need not be unique, and anything it names TRAVELS — this path
        # is also the payload writer's source, which copies it into `bundles/` under whatever it
        # is called. A name that is not `*.agentpkg` there is invisible to the engine, which
        # globs for exactly that: an app that installs, opens, and contains no agent.
        package = work / "upload.agentpkg"
        package.write_bytes(submission.package)

        try:
            manifest = bundle_io.read_manifest(package)
        except BundleError as e:
            return IntakeResult(BAD_REQUEST, str(e), creator_id=creator.id)

        claimed = (submission.bundle_id or "").strip()
        if claimed and claimed != manifest.id:
            # A mismatch is how an id-squatting attempt would look, and it is also a plain client
            # bug. Either way the manifest is the authority and disagreeing is worth refusing.
            return IntakeResult(
                BAD_REQUEST,
                f"the upload claims bundle id '{claimed}' but its manifest says '{manifest.id}'.",
                creator_id=creator.id,
            )

        owner = self._creators.claim(manifest.id, creator.id)
        if owner != creator.id:
            return IntakeResult(
                CONFLICT,
                f"the bundle id '{manifest.id}' already belongs to another creator. Rename your "
                "agent and publish again.",
                bundle_id=manifest.id,
                creator_id=creator.id,
            )

        # THE ARTIFACT'S NAME COMES FROM THE MANIFEST, never from the upload. A client's filename is
        # attacker-controlled and need not be unique: two authors posting a file both called
        # `upload.agentpkg` would land on the same S3 key, and the second would overwrite the
        # first's package while its index row still pointed there — a silent swap of one agent's
        # download for another's. `<id>-<version>.agentpkg` is the same convention `bundle pack`
        # writes, so the registry looks identical whichever tool published.
        artifact_name = f"{manifest.id}-{manifest.version}.agentpkg"
        # Both `id` and `version` were validated by the manifest parser, so this is a safe file
        # name as well as a safe S3 key — and using the SAME string for both is what makes the
        # bundle inside the installer's payload identical to the one in the registry.
        package = package.rename(work / artifact_name)

        index = self._store.read_index() or {}
        published = next(
            (
                b
                for b in (index.get("bundles") or [])
                if isinstance(b, dict) and str(b.get("id") or "") == manifest.id
            ),
            None,
        )
        if published and not is_update(str(published.get("version") or "0"), manifest.version):
            return IntakeResult(
                CONFLICT,
                f"{manifest.id} {published.get('version')} is already published and "
                f"{manifest.version} is not newer. Bump `version` in agent.toml — installs "
                "supersede BY VERSION, so republishing the same number reaches nobody.",
                bundle_id=manifest.id,
                version=manifest.version,
                creator_id=creator.id,
            )

        # SCHEMA 1 CANNOT HOLD A CREATOR'S BUNDLE. In schema 1 every client verifies every entry
        # against the ONE pinned publisher_key; `publisher_id` does not exist there. An entry signed
        # with a creator's own key would therefore fail verification on every installed machine —
        # fail-closed, so the store would show it and the download would refuse, with nothing
        # anywhere to say why. Refuse here instead, and name the one command that fixes it.
        if index and int(index.get("schema") or 1) < 2:
            return IntakeResult(
                CONFLICT,
                "this registry is still schema 1 (one publisher key for everything), so a bundle "
                "signed with your creator key could not be verified by anyone. An operator has to "
                "migrate it first:  agentd bundle roster add --root-key <keypair> --id <you> "
                "--key <your public key>  then  agentd bundle roster publish --to <registry>. "
                "Nothing was published.",
                bundle_id=manifest.id,
                version=manifest.version,
                creator_id=creator.id,
            )

        installer, warnings = self._build_installer(package, work)

        # ---- artifacts, then the index. Under the lock, because the index is read-modify-write.
        with self._lock:
            # Re-read INSIDE the lock: the copy above was for the cheap version check, and by now
            # another publish may have committed. Merging against a stale index is the bug the lock
            # exists to prevent, so it must not be defeated by reusing that read.
            index = self._store.read_index() or {}
            entry = self._entry(manifest, package, creator)
            url = self._store.put_artifact(
                artifact_name, submission.package, "application/octet-stream"
            )
            entry["url"] = artifact_name  # relative, like every other registry url

            installer_url = ""
            if installer is not None:
                data = installer.read_bytes()
                installer_url = self._store.put_artifact(
                    installer.name, data, "application/octet-stream"
                )
                entry["installers"] = [self._installer_row(installer, data, creator)]

            self._store.write_index(self._merge(index, entry))

        log.info("published %s %s for %s", manifest.id, manifest.version, creator.id)
        return IntakeResult(
            OK,
            f"published {manifest.id} {manifest.version}."
            + ("" if installer else " (bundle only — no installer, see warnings)"),
            bundle_id=manifest.id,
            version=manifest.version,
            url=url,
            installer_url=installer_url,
            creator_id=creator.id,
            warnings=warnings,
        )

    # ================================================================== helpers
    def _build_installer(self, package: Path, work: Path) -> tuple[Path | None, list[str]]:
        """The per-agent stub, or None with the reasons. Never raises — see the module docstring."""
        if self._products is None:
            return None, [
                "no installer was built: this service has no product builder configured, so only "
                "people who already have agentd can install this agent."
            ]
        from agent_runtime.application.interfaces.product import ProductSource

        try:
            build = self._products.build(
                ProductSource(package=package), work / "payload", work / "out"
            )
        except Exception as e:  # noqa: BLE001 — a build failure must not lose the publish
            log.warning("intake: stub build failed: %s", e)
            return None, [f"no installer was built (the build failed: {e})"]
        return build.stub, list(build.warnings)

    def _entry(self, manifest, package: Path, creator) -> dict:
        from agent_runtime.infrastructure.marketplace import bundle_io

        digest = bundle_io.sha256_file(package)
        return {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "agentd_compat": manifest.agentd_compat,
            "entitlement": manifest.entitlement,
            "price": "free" if not manifest.entitlement else "paid",
            "icon": manifest.icon,
            "sha256": digest,
            "size": package.stat().st_size,
            # WHOSE key signed this. Clients look the creator up on the roster and verify against
            # that key — never against the pinned root key, which signs only the roster.
            "publisher_id": creator.id,
            "sig": self._signer.sign(creator.id, digest.encode("ascii")),
        }

    def _installer_row(self, installer: Path, data: bytes, creator) -> dict:
        import hashlib

        from agent_runtime.infrastructure.marketplace.index_builder import PLATFORM_BY_EXT

        digest = hashlib.sha256(data).hexdigest()
        return {
            "platform": PLATFORM_BY_EXT.get(installer.suffix.lower(), "win"),
            "url": installer.name,
            "size": len(data),
            "sha256": digest,
            # Signed SEPARATELY from the bundle: the entry signature covers only the .agentpkg
            # digest, so an unsigned installer row would let anyone able to write to the registry
            # repoint the download without breaking a single signature.
            "sig": self._signer.sign(creator.id, digest.encode("ascii")),
        }

    @staticmethod
    def _merge(index: dict, entry: dict) -> dict:
        """This entry replaces its own id; every other creator's rows are carried EXACTLY.

        Never a rebuild. Another creator's bundle is signed with a key this service does not have
        and must never have, so touching those rows is impossible as well as wrong — and a rebuild
        from a directory holding one new package is how a whole marketplace gets unpublished.
        """
        merged = dict(index)
        rows = [
            b
            for b in (index.get("bundles") or [])
            if isinstance(b, dict) and str(b.get("id") or "") != str(entry["id"])
        ]
        rows.append(entry)
        rows.sort(key=lambda b: (str(b.get("id") or ""), str(b.get("version") or "")))
        merged["bundles"] = rows
        # Schema 2 only. Creators sign their own bundles and the pinned key is the platform ROOT,
        # which is the only shape that can hold more than one publisher. An EXISTING schema-1
        # registry is refused earlier rather than upgraded here: flipping the number without a
        # signed roster would leave every carried entry attributed to a key nobody can look up.
        merged["schema"] = 2
        merged.setdefault("publishers", {})
        return merged
