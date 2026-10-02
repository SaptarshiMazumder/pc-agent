"""S3UploadSlots — per-account, size-bounded upload slots in the registry bucket's PRIVATE space.

    pending/uploads/<owner digest>/<random>.agentpkg

UNDER ``pending/``, ON PURPOSE. That prefix is already carved out of the bucket's public-read grant
and is the one place the publish role may delete (infra/modules/registry.tf, publish.tf), which is
exactly what an unreviewed upload needs: nobody can download it from the registry's domain, and it
is removed the moment it is claimed. Creator ids are hex digests, so ``uploads`` can never collide
with a parked package's ``pending/<creator_id>/``.

A PRESIGNED POST, NOT A PRESIGNED PUT. Only a POST policy can bound the size
(``content-length-range``); a PUT url accepts whatever length the holder sends.

THE OWNER IS IN THE KEY, as a digest rather than the raw account id: `take` refuses any key outside
the caller's own segment, so a slot key is useless to anyone but the account that reserved it.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from agent_runtime.application.interfaces.publish_intake import UploadSlot

log = logging.getLogger("agentd")

UPLOADS_PREFIX = "pending/uploads"
SUFFIX = ".agentpkg"
CONTENT_TYPE = "application/octet-stream"
EXPIRES_SECONDS = 900


class S3UploadSlots:
    def __init__(
        self,
        s3_client,
        bucket: str,
        prefix: str = UPLOADS_PREFIX,
        expires_in: int = EXPIRES_SECONDS,
    ):
        self._s3 = s3_client
        self._bucket = bucket
        self._prefix = (prefix or UPLOADS_PREFIX).strip("/")
        self._expires_in = expires_in

    def _owner_prefix(self, owner: str) -> str:
        digest = hashlib.sha256(f"agentd-upload:{owner}".encode()).hexdigest()[:32]
        return f"{self._prefix}/{digest}/"

    # ------------------------------------------------------------------ port
    def reserve(self, owner: str, max_bytes: int) -> UploadSlot:
        key = f"{self._owner_prefix(owner)}{uuid.uuid4().hex}{SUFFIX}"
        post = self._s3.generate_presigned_post(
            Bucket=self._bucket,
            Key=key,
            Fields={"Content-Type": CONTENT_TYPE},
            Conditions=[{"Content-Type": CONTENT_TYPE}, ["content-length-range", 1, max_bytes]],
            ExpiresIn=self._expires_in,
        )
        return UploadSlot(
            key=key, url=post["url"], fields=dict(post["fields"]), expires_in=self._expires_in
        )

    def take(self, owner: str, key: str) -> bytes:
        if not key.startswith(self._owner_prefix(owner)) or not key.endswith(SUFFIX):
            log.warning("upload claim refused: key is not this account's slot")
            return b""
        if ".." in key.split("/"):
            return b""
        try:
            body = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except Exception as e:  # noqa: BLE001 — a missing upload is an answer, not a crash
            if "NoSuchKey" in type(e).__name__ or "NoSuchKey" in str(e) or "404" in str(e):
                return b""
            raise
        self._s3.delete_object(Bucket=self._bucket, Key=key)
        return body
