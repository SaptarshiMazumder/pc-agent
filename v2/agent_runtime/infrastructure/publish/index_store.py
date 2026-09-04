"""S3IndexStore + DynamoIndexLock — the registry's storage, and the mutex over its index.

THE LOCK IS NOT OPTIONAL. index.json is the registry's entire contents, and publishing is a
read-modify-write of it. Two authors publishing in the same second would each read the index, add
their own row, and write back — the second write deleting the first author's bundle. No error
anywhere; an agent simply vanishes from the marketplace. S3 has no transactions, so a DynamoDB
conditional write is the mutex.

THE LEASE EXPIRES. A Lambda killed mid-publish would otherwise hold the lock forever and no one
could ever publish again. So the item carries an expiry and a stale lease is taken over — the TTL is
set well beyond the function's own timeout, so an overrun cannot let two publishes overlap.

INDEX LAST, ALWAYS. That ordering lives in the intake service, not here, but it is why this class
separates ``put_artifact`` from ``write_index`` instead of offering one "publish everything" call:
the index is what makes a URL public, so every artifact it names must already exist.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

log = logging.getLogger("agentd")

INDEX_KEY = "index.json"


class S3IndexStore:
    def __init__(self, s3_client, bucket: str, prefix: str = "", public_base: str = ""):
        """:param public_base: the URL artifacts are served from. Empty => derived from the bucket's
        regional endpoint. Injected because a CDN or custom domain later changes only this."""
        self._s3 = s3_client
        self._bucket = bucket
        self._prefix = (prefix or "").strip("/")
        self._public_base = (public_base or "").rstrip("/")

    def scoped(self, scope: str) -> "S3IndexStore":
        """This store, re-rooted at one organization's private registry (``orgs/<org_id>/`` under
        the current prefix). Empty scope returns self — the public registry is the unscoped one.

        The bucket, client and public base are shared deliberately: an org registry is a PREFIX,
        not a second deployment, so nothing about artifact serving or the lock changes with it.
        """
        scope = (scope or "").strip().strip("/")
        if not scope:
            return self
        return S3IndexStore(
            self._s3,
            self._bucket,
            prefix=f"{self._prefix}/orgs/{scope}" if self._prefix else f"orgs/{scope}",
            public_base=self._public_base,
        )

    def presign(self, name: str, expires_s: int = 3600) -> str:
        """A time-limited GET url for one artifact in THIS store's space.

        For the private shelves. `orgs/*` is carved out of the bucket's public-read grant, so a
        member's client cannot fetch an org bundle by url the way it fetches a marketplace one.
        The publish service authenticates the member, checks the membership, and hands back links
        made here -- the grant travels with the link and expires, instead of the prefix being
        readable by anyone who learns an org id.
        """
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": self._key(name)},
            ExpiresIn=int(expires_s),
        )

    def _key(self, name: str) -> str:
        return f"{self._prefix}/{name}" if self._prefix else name

    def read_index(self) -> dict:
        """{} when the registry is empty. A MISSING index is a new registry; a CORRUPT one raises,
        because merging into something we could not parse would publish a registry that drops every
        bundle already in it."""
        try:
            body = self._s3.get_object(Bucket=self._bucket, Key=self._key(INDEX_KEY))["Body"].read()
        except Exception as e:  # noqa: BLE001 — botocore raises NoSuchKey/ClientError
            name = type(e).__name__
            if "NoSuchKey" in name or "NoSuchKey" in str(e) or "404" in str(e):
                return {}
            raise
        return json.loads(body.decode("utf-8"))

    def write_index(self, index: dict) -> None:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=self._key(INDEX_KEY),
            Body=(json.dumps(index, indent=2) + "\n").encode("utf-8"),
            ContentType="application/json",
            # index.json is read on every store open and changes on every publish. Without this a
            # CDN or browser can serve a stale listing for hours, so a successful publish looks
            # like it did nothing.
            CacheControl="no-cache",
        )
        self._write_catalog(index)

    def _write_catalog(self, index: dict) -> None:
        """The public marketplace page's view of the index, refreshed with it.

        HERE, and not in the service, for two reasons. The base url artifacts are served from is
        THIS class's knowledge — nothing above it knows whether that is a bucket endpoint, a CDN
        or a custom domain. And every writer of the index reaches it through this one method
        (publishing, and roster admission re-signing), so the catalog cannot be left stale by a
        code path that forgot about it.

        AFTER the index, and never fatal. index.json is the registry: if this fails, the store
        page serves a listing one publish old, which is a far better outcome than a publish that
        reports failure for an artifact nothing installs from.
        """
        from agent_runtime.domain.bundle import parse_registry_index
        from agent_runtime.domain.catalog import CATALOG_FILENAME, build_catalog

        try:
            # Trailing slash: `urljoin("https://host/registry", "x.agentpkg")` REPLACES the last
            # path segment and yields https://host/x.agentpkg — the prefix silently dropped, and
            # every download in the store 404s.
            catalog = build_catalog(parse_registry_index(index), base=self.base_url + "/")
            self._s3.put_object(
                Bucket=self._bucket,
                Key=self._key(CATALOG_FILENAME),
                Body=(json.dumps(catalog, indent=2) + "\n").encode("utf-8"),
                ContentType="application/json",
                CacheControl="no-cache",
            )
        except Exception:  # noqa: BLE001 — see the docstring
            log.warning("could not write %s (the index published fine)", CATALOG_FILENAME, exc_info=True)

    def put_artifact(self, name: str, data: bytes, content_type: str) -> str:
        self._s3.put_object(
            Bucket=self._bucket, Key=self._key(name), Body=data, ContentType=content_type
        )
        return f"{self.base_url}/{name}"

    @property
    def base_url(self) -> str:
        if self._public_base:
            return self._public_base
        region = getattr(getattr(self._s3, "meta", None), "region_name", "") or "us-east-1"
        root = f"https://{self._bucket}.s3.{region}.amazonaws.com"
        return f"{root}/{self._prefix}" if self._prefix else root


class DynamoIndexLock:
    """A leased mutex on one DynamoDB item. Used as a context manager."""

    def __init__(self, table, name: str = "registry-index", ttl_seconds: int = 900, attempts: int = 20,
                 wait_seconds: float = 1.0, sleep=None, now=None):
        self._table = table
        self._name = name
        self._ttl = ttl_seconds
        self._attempts = attempts
        self._wait = wait_seconds
        self._sleep = sleep or time.sleep
        self._now = now or time.time
        self._token = ""

    def __enter__(self):
        for attempt in range(self._attempts):
            if self._try_acquire():
                return self
            if attempt == 0:
                log.info("index lock held by another publish — waiting")
            self._sleep(self._wait)
        # Raising is right: publishing without the lock is the exact bug this prevents, and the
        # caller turns this into "another publish is in progress, try again" rather than corrupting
        # the index.
        raise TimeoutError(
            "another publish is in progress and did not finish in time. Nothing was published — "
            "try again in a moment."
        )

    def __exit__(self, *exc) -> None:
        self._release()

    # ------------------------------------------------------------------ internals
    def _try_acquire(self) -> bool:
        token = uuid.uuid4().hex
        expires = int(self._now()) + self._ttl
        try:
            self._table.put_item(
                Item={"lock_id": self._name, "token": token, "expires": expires},
                # Free, OR the previous holder's lease has expired. The second clause is what keeps
                # a function killed mid-publish from locking the registry permanently.
                ConditionExpression="attribute_not_exists(lock_id) OR expires < :now",
                ExpressionAttributeValues={":now": int(self._now())},
            )
        except Exception as e:  # noqa: BLE001
            if "ConditionalCheckFailed" in type(e).__name__ or "ConditionalCheckFailed" in str(e):
                return False
            raise
        self._token = token
        return True

    def _release(self) -> None:
        if not self._token:
            return
        try:
            self._table.delete_item(
                Key={"lock_id": self._name},
                # Only delete OUR lease. Without this, a publish that overran its TTL would delete
                # the lock a *different* publish is now holding.
                ConditionExpression="#t = :token",
                ExpressionAttributeNames={"#t": "token"},
                ExpressionAttributeValues={":token": self._token},
            )
        except Exception as e:  # noqa: BLE001 — losing a lease is not worth failing a publish over
            log.info("index lock release skipped: %s", e)
        finally:
            self._token = ""
