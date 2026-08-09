"""S3ParkedStore — first-publish uploads, held privately until their creator is admitted.

SAME BUCKET AS THE REGISTRY, DIFFERENT WORLD. Everything else in that bucket exists to be
world-readable; a parked package is the opposite — unreviewed, unsigned content that must not be
downloadable from the registry's own domain before an operator has looked at who sent it. The
bucket policy therefore carves ``pending/*`` out of the public-read statement (see
infra/modules/registry.tf); only the publish Lambda's role reaches it.

ONE KEY PER (creator, bundle): ``pending/<creator_id>/<bundle_id>.agentpkg``. A re-upload before
admission overwrites — the author pressing Publish twice while waiting means "use the newer one",
never "hold both".
"""

from __future__ import annotations

import logging

from agent_runtime.application.interfaces.publish_intake import ParkedPackage

log = logging.getLogger("agentd")

PENDING_PREFIX = "pending"
SUFFIX = ".agentpkg"


class S3ParkedStore:
    def __init__(self, s3_client, bucket: str, prefix: str = PENDING_PREFIX):
        self._s3 = s3_client
        self._bucket = bucket
        self._prefix = (prefix or PENDING_PREFIX).strip("/")

    def _key(self, creator_id: str, bundle_id: str) -> str:
        return f"{self._prefix}/{creator_id}/{bundle_id}{SUFFIX}"

    # ------------------------------------------------------------------ port
    def park(self, creator_id: str, bundle_id: str, package: bytes) -> None:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=self._key(creator_id, bundle_id),
            Body=package,
            ContentType="application/octet-stream",
        )
        log.info("parked %s for %s (%d bytes)", bundle_id, creator_id, len(package))

    def parked(self, creator_id: str) -> list[ParkedPackage]:
        out: list[ParkedPackage] = []
        kwargs = {"Bucket": self._bucket, "Prefix": f"{self._prefix}/{creator_id}/"}
        while True:
            page = self._s3.list_objects_v2(**kwargs)
            for item in page.get("Contents") or []:
                key = str(item.get("Key") or "")
                name = key.rsplit("/", 1)[-1]
                if not name.endswith(SUFFIX):
                    continue  # not ours — never delete or replay a key this class did not write
                out.append(
                    ParkedPackage(
                        creator_id=creator_id,
                        bundle_id=name[: -len(SUFFIX)],
                        size=int(item.get("Size") or 0),
                        parked_at=str(item.get("LastModified") or ""),
                    )
                )
            if not page.get("IsTruncated"):
                return sorted(out, key=lambda p: p.bundle_id)
            kwargs["ContinuationToken"] = page.get("NextContinuationToken")

    def retrieve(self, creator_id: str, bundle_id: str) -> bytes:
        try:
            body = self._s3.get_object(
                Bucket=self._bucket, Key=self._key(creator_id, bundle_id)
            )["Body"].read()
        except Exception as e:  # noqa: BLE001 — a vanished parked package is handled, not fatal
            if "NoSuchKey" in type(e).__name__ or "NoSuchKey" in str(e) or "404" in str(e):
                return b""
            raise
        return body

    def remove(self, creator_id: str, bundle_id: str) -> None:
        # Idempotent by S3's own semantics: deleting a missing key succeeds.
        self._s3.delete_object(Bucket=self._bucket, Key=self._key(creator_id, bundle_id))
