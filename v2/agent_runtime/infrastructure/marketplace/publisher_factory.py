"""publisher_for — choose a publishing adapter from the TARGET, not from a flag.

    https://…            -> HttpRegistryPublisher   (an author: no key, no bucket, just sign-in)
    s3://… or a path     -> S3RegistryPublisher     (an operator: holds the registry's key)

Reading it off the target is deliberate. A flag would be a second thing to keep consistent with
`publish_target`, and getting them out of step means either an author being asked for a signing key
they cannot have, or a release tool quietly POSTing to a service. The target already says which
world it is.

EMPTY IS NOT A DEFAULT. No target => no publisher, and the caller reports what to configure. That
empty default is what stops a DOWNLOADED copy of this product from publishing into someone else's
marketplace merely because the tool is present.
"""

from __future__ import annotations

from agent_runtime.infrastructure.marketplace.http_publisher import HttpRegistryPublisher
from agent_runtime.infrastructure.marketplace.s3_publisher import S3RegistryPublisher
from agent_runtime.infrastructure.products.agent_packer import BundleAgentPacker

HTTP_SCHEMES = ("http://", "https://")


def is_service_target(target: str) -> bool:
    return str(target or "").strip().lower().startswith(HTTP_SCHEMES)


def publisher_for(config, target: str = ""):
    """-> a BundlePublisher, or None when this install has no publish target at all."""
    resolved = str(target or getattr(config, "publish_target", "") or "").strip()
    if not resolved:
        return None
    if is_service_target(resolved):
        return HttpRegistryPublisher(
            service_url=resolved,
            config=config,
            packer=BundleAgentPacker(),
        )
    return S3RegistryPublisher(
        target=resolved, keyfile=str(getattr(config, "publisher_keyfile", "") or "")
    )
