"""BundlePublisher — the seam between "an author wants to publish" and "how it reaches the store".

WHY THIS EXISTS. Publishing was one function that assumed the publisher was US: it needed an
ed25519 private key ON the author's disk and AWS credentials with write access to the registry
bucket. That is exactly right for a release engineer and impossible for the person this whole
product is for — someone who installed the app, built an agent, and wants it listed. You cannot
hand every author the key that signs the marketplace.

So there are two ways to publish, and the difference is WHO HOLDS THE KEY:

  ``S3RegistryPublisher``    the operator path. Rebuild the whole index locally, sign it with the
                            registry's own key, upload artifacts then index. Unchanged, still the
                            release tool, still the only thing that can rotate a key or a roster.
  ``HttpRegistryPublisher``  the author path. POST the .agentpkg to the publish service, which
                            authenticates the account, resolves it to a creator, and signs with
                            THAT creator's key server-side. The author needs no key and no bucket.

`publish_agent` depends on this protocol and picks an adapter from the target's scheme, so the tool
loses its key requirement without losing any of the guards — the S3 adapter is still literally the
same code path `agentd bundle publish` runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PublishRequest:
    agent_dir: Path
    dry_run: bool = True
    version: str = ""  # "" => whatever the agent's own precedence chain decides
    # Publish the per-agent INSTALLER alongside the bundle, so a stranger with no agentd can
    # download it from the marketplace. Requires a built stub (or a toolchain to build one).
    with_installer: bool = True


@dataclass
class PublishResult:
    """What happened, in terms a person can act on.

    ``pending`` is a first-class NORMAL outcome, not an error: a creator's first publish files for
    review, and reporting that as a failure would send authors to look for a bug in their agent.
    """

    ok: bool
    message: str
    bundle_id: str = ""
    version: str = ""
    url: str = ""
    installer_url: str = ""
    pending: bool = False  # accepted, awaiting review before it is listed
    dry_run: bool = False
    detail: str = ""  # the full transcript / server response, for the curious
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class BundlePublisher(Protocol):
    """Gets one agent from a directory into a registry other people install from."""

    name: str  # for messages: "the publish service at https://…" / "s3://bucket/prefix"

    def requirements(self) -> list[str]:
        """What is missing before this publisher can run, as user-facing sentences.

        Empty means ready. Returned rather than raised because the answer is configuration advice
        ("set publish_target") and the caller wants to show all of it at once, before building
        anything.
        """
        ...

    def publish(self, request: PublishRequest) -> PublishResult:
        """Never raises for an expected failure — an unreachable service, a rejected version and a
        duplicate id are all results with a message."""
        ...
