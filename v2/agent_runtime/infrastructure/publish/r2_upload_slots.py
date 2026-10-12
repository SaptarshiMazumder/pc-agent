"""Private R2 PUT uploads with atomic one-time ownership and bounded consumption.

R2 has no presigned POST policies. PUT accepts arbitrary stored length; enforce
the package ceiling on HEAD and bounded GET before intake. Expire uploads with
a bucket lifecycle rule as well as this metadata check.
"""

import time
import uuid

from agent_runtime.application.interfaces.publish_intake import UploadSlot


class R2UploadSlots:
    def __init__(self, s3, bucket, database, expires_in=900):
        self.s3, self.bucket, self.database, self.expires_in = s3, bucket, database, expires_in

    def reserve(self, owner, max_bytes):
        key = f"pending/uploads/{uuid.uuid4().hex}.agentpkg"
        expires = int(time.time()) + self.expires_in
        with self.database.connect() as db:
            db.execute("DELETE FROM uploads WHERE expires<?", (int(time.time()) - 86400,))
            db.execute(
                "INSERT INTO uploads (key,owner,max_bytes,expires) VALUES (?,?,?,?)",
                (key, owner, max_bytes, expires),
            )
        url = self.s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": "application/octet-stream"},
            ExpiresIn=self.expires_in,
        )
        return UploadSlot(
            key=key,
            url=url,
            fields={},
            expires_in=self.expires_in,
            method="PUT",
            headers={"Content-Type": "application/octet-stream"},
        )

    def take(self, owner, key):
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM uploads WHERE key=? AND owner=? AND claimed=0 AND expires>?",
                (key, owner, int(time.time())),
            ).fetchone()
            if not row:
                return b""
            db.execute("UPDATE uploads SET claimed=1 WHERE key=?", (key,))
        try:
            size = int(self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
            if size < 1 or size > row["max_bytes"]:
                return b""
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            stream = response["Body"]
            try:
                body = stream.read(row["max_bytes"] + 1)
            finally:
                stream.close()
            return body if 0 < len(body) <= row["max_bytes"] else b""
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return b""
            raise
        finally:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
