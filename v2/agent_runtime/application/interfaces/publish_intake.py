"""Publish-intake ports — what a PUBLISH SERVICE needs from the world around it.

This is the server side of ``bundle_publisher.py``. An author POSTs a ``.agentpkg`` and their
ordinary session token; the service turns that into a signed, listed marketplace entry plus the
small installer a stranger downloads. Everything it touches from the outside is a port here, so the
orchestration is unit-testable with fakes and holds no AWS knowledge at all.

WHY THE SERVICE SIGNS AND BUILDS, rather than accepting either from the author:

  * SIGNING. The installer must be signed with the PLATFORM's code-signing certificate or the
    person who downloads it gets a SmartScreen warning — which is the whole reason the engine+stub
    split exists. That certificate cannot be handed to authors.
  * BUILDING. Whoever signs must compile. If an author compiled the installer, the service would
    be stamping its certificate on a binary it cannot see inside — an NSIS script runs arbitrary
    code at install time, unsandboxed, as the user. Verifying an author's binary means rebuilding
    it and comparing, and at that point the author's build was pointless. So: the service compiles
    from ITS OWN stub template, and the only thing the author contributes is data in a payload.

The two trust layers stay distinct, and that is what makes signing an author's package acceptable:

    installer behaviour   <- the platform certificate, over a script the platform wrote
    the agent's own code  <- the creator's ed25519 signature + the plugin sandbox, revocable by
                             removing them from the roster, with nothing to re-sign

ONE KEY PER CREATOR, NEVER THE ROOT KEY. The root key signs the ROSTER (who is a creator) and is
offline. Each creator's own key signs their own bundles, so this service verifies against a key the
roster ALREADY lists and never needs the root private key. That is precisely what makes publishing
safe to run as a web service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# HTTP statuses the intake speaks in. Kept here so the service and every transport adapter agree,
# and so the two NON-ERROR outcomes are impossible to miss.
OK = 200
PENDING = 202  # accepted, awaiting operator review — a NORMAL result, not a failure
BAD_REQUEST = 400
UNAUTHORIZED = 401
FORBIDDEN = 403
CONFLICT = 409
TOO_LARGE = 413
SERVER_ERROR = 500

# Creator states. A creator exists from their first submission; being LISTED is what makes their
# bundles verifiable by installed clients, and only the offline root key can do that.
PENDING_REVIEW = "pending"
LISTED = "listed"
REVOKED = "revoked"


@dataclass(frozen=True)
class Submission:
    """One publish request, already parsed out of whatever transport carried it."""

    package: bytes
    token: str  # the author's accounts session token
    bundle_id: str = ""  # what they CLAIM to be publishing; checked against the manifest
    filename: str = ""


@dataclass(frozen=True)
class Creator:
    """A publishing identity. Deliberately carries NO private key material."""

    id: str
    account_id: str = ""
    name: str = ""
    state: str = PENDING_REVIEW
    public_key: str = ""

    @property
    def may_publish(self) -> bool:
        return self.state == LISTED


@dataclass
class IntakeResult:
    status: int
    message: str
    bundle_id: str = ""
    version: str = ""
    url: str = ""
    installer_url: str = ""
    creator_id: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in (OK, PENDING)

    @property
    def pending(self) -> bool:
        return self.status == PENDING

    def body(self) -> dict:
        """The JSON an author's client parses. Field names match what HttpRegistryPublisher reads."""
        payload = {
            "message": self.message,
            "bundle_id": self.bundle_id,
            "version": self.version,
            "creator_id": self.creator_id,
        }
        if self.url:
            payload["url"] = self.url
        if self.installer_url:
            payload["installer_url"] = self.installer_url
        if self.warnings:
            payload["warnings"] = self.warnings
        return payload


@dataclass(frozen=True)
class ParkedPackage:
    """A first-publish upload waiting for its creator to be admitted.

    The upload used to be DISCARDED on 202, which quietly turned "publish" into a two-click flow:
    the author had to notice they were approved and press Publish again. Parking is what lets
    admission COMPLETE the publish instead — one click for the author, and the review step stays.
    """

    creator_id: str
    bundle_id: str
    size: int = 0
    parked_at: str = ""


@runtime_checkable
class Authenticator(Protocol):
    """Session token -> the account it belongs to, or None."""

    def account(self, token: str) -> dict | None:
        """Never raises: an unreachable accounts service is None, i.e. unauthorized."""
        ...


@runtime_checkable
class IntakeParker(Protocol):
    """Holds not-yet-approved uploads, PRIVATELY — never in the world-readable registry space.

    One slot per (creator, bundle id): a re-upload before admission replaces the previous attempt,
    which is the only sane reading of an author pressing Publish twice while waiting.
    """

    def park(self, creator_id: str, bundle_id: str, package: bytes) -> None: ...

    def parked(self, creator_id: str) -> list[ParkedPackage]: ...

    def retrieve(self, creator_id: str, bundle_id: str) -> bytes:
        """b'' when gone — a parked package disappearing is handled, never fatal."""
        ...

    def remove(self, creator_id: str, bundle_id: str) -> None:
        """Idempotent. Called after the parked publish completed (or was refused for good)."""
        ...


@runtime_checkable
class RootKeyVault(Protocol):
    """The platform ROOT key, for the ONE thing it signs: the roster.

    This port exists so admission can run as a service. The offline-file deployment stays possible
    (the CLI reads the keypair file directly and never touches this), but a vault adapter holding
    the key KMS-wrapped is what removes the single operator laptop from the loop: any authorised
    admin triggers a re-sign, the plaintext exists only inside the signing call, and every use is
    in the audit trail.
    """

    def private_key(self) -> str:
        """The base64 private half, decrypted in memory. Raises when the vault holds no key —
        callers must fail loudly; a roster signed with the wrong key bricks the registry."""
        ...

    def public_key(self) -> str: ...


@runtime_checkable
class CreatorDirectory(Protocol):
    """Who may publish, and which bundle ids they own."""

    def for_account(self, account_id: str, display_name: str = "") -> Creator:
        """The creator for this account, CREATING one in `pending` on first sight.

        First-publish-files-for-review is not a limitation to work around: the roster is signed by
        an offline root key, so admitting a creator cannot be automated without putting that key
        online. It is the review step a marketplace needs anyway.
        """
        ...

    def claim(self, bundle_id: str, creator_id: str) -> str:
        """Record ``creator_id`` as the owner of ``bundle_id``; return the ACTUAL owner.

        Must be atomic (a conditional write). A bundle id belongs to the first creator who
        publishes it, and this is the entire squatting/hijack surface: two creators racing the same
        id must not both succeed. A return value different from ``creator_id`` means refused.
        """
        ...


@runtime_checkable
class BundleSigner(Protocol):
    """Signs on a creator's behalf with THEIR key. The root key is never reachable from here."""

    def sign(self, creator_id: str, message: bytes) -> str:
        """base64 ed25519 signature. Raises when that creator has no usable key."""
        ...

    def public_key(self, creator_id: str) -> str:
        """The base64 public half, for the roster the operator later signs."""
        ...


@runtime_checkable
class IndexStore(Protocol):
    """The registry's storage: the index document plus the artifacts it points at."""

    def read_index(self) -> dict:
        """The current index.json, or {} when the registry is empty."""
        ...

    def write_index(self, index: dict) -> None:
        """Write index.json. Called LAST, always — see the service."""
        ...

    def put_artifact(self, name: str, data: bytes, content_type: str) -> str:
        """Store one artifact; return the URL it will be served from."""
        ...


@runtime_checkable
class IndexLock(Protocol):
    """Serialises the index's read-modify-write across concurrent publishes.

    S3 has no transactions, so two authors publishing at the same moment would each read the index,
    add their own entry, and write back — and the second write would silently delete the first
    author's bundle. Used as a context manager; raising on failure to acquire is correct, because
    publishing without the lock is the bug this prevents.
    """

    def __enter__(self) -> object: ...

    def __exit__(self, *exc) -> None: ...
