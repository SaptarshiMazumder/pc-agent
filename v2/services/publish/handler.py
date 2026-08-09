"""The publish service — `POST /registry/publish`. The Lambda's composition root and HTTP adapter.

This file is deliberately thin: parse the request, wire the adapters, hand a ``Submission`` to
``PublishIntakeService``, render its result. Every decision lives in that service, which is tested
with fakes and knows nothing about AWS.

WHY A LAMBDA AND NOT AN RPC ON THE DAEMON. Publishing needs S3 write and a signing key. Putting it
on the runtime container would give the thing that executes agents' code write access to the store
those agents are sold from — app connections must never be one bug away from publishing. A separate
function with its own tiny role is the whole point.

WHY A CONTAINER IMAGE. It needs `makensis` on the filesystem to compile the per-agent installer, and
a zip-packaged Lambda cannot install a system package. NSIS cross-compiles, so a Linux function
produces Windows installers.

CONFIGURED BY ENVIRONMENT, entirely. Nothing here has a default bucket, table or url — a wrong
default would publish to the wrong registry, and the failure would be a live one.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("agentd")
logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))

PUBLISH_ROUTE = "/registry/publish"


# ────────────────────────────── composition ──────────────────────────────


def build_service():
    """Wire the intake service from environment. Cached across invocations by the module scope."""
    import boto3

    from agent_runtime.application.services.publish_intake_service import PublishIntakeService
    from agent_runtime.infrastructure.publish.authenticator import AccountsAuthenticator
    from agent_runtime.infrastructure.publish.creator_directory import DynamoCreatorDirectory
    from agent_runtime.infrastructure.publish.index_store import DynamoIndexLock, S3IndexStore
    from agent_runtime.infrastructure.publish.signer import DirectoryBundleSigner, KmsEnvelopeSigner

    def required(name: str) -> str:
        value = (os.environ.get(name) or "").strip()
        if not value:
            raise RuntimeError(f"{name} is not set — the publish service cannot start without it")
        return value

    dynamodb = boto3.resource("dynamodb")
    creators_table = dynamodb.Table(required("CREATORS_TABLE"))
    owners_table = dynamodb.Table(required("BUNDLE_OWNERS_TABLE"))
    locks_table = dynamodb.Table(required("LOCKS_TABLE"))

    # KMS is how a leaked table dump stays worthless. Without a key id the private halves are stored
    # in the clear, which is fine for a local run and stated rather than silently assumed.
    kms_key = (os.environ.get("KMS_KEY_ID") or "").strip()
    if kms_key:
        envelope = KmsEnvelopeSigner(None, boto3.client("kms"), kms_key)
        directory = DynamoCreatorDirectory(
            creators_table, owners_table, key_factory=envelope.key_factory()
        )
        envelope._directory = directory  # noqa: SLF001 — the pair is mutually recursive by design
        signer = envelope
    else:
        log.warning("KMS_KEY_ID unset — creator private keys are stored UNENCRYPTED")
        directory = DynamoCreatorDirectory(creators_table, owners_table)
        signer = DirectoryBundleSigner(directory)

    store = S3IndexStore(
        boto3.client("s3"),
        bucket=required("REGISTRY_BUCKET"),
        prefix=os.environ.get("REGISTRY_PREFIX", ""),
        public_base=os.environ.get("REGISTRY_PUBLIC_BASE", ""),
    )
    lock = DynamoIndexLock(locks_table, ttl_seconds=int(os.environ.get("LOCK_TTL_SECONDS", "900")))

    return PublishIntakeService(
        authenticator=AccountsAuthenticator(required("ACCOUNTS_URL")),
        creators=directory,
        signer=signer,
        index_store=store,
        lock=lock,
        product_service=_product_service(store),
    )


def _product_service(store):
    """The stub builder, or None with a warning.

    Configured from the SAME registry this service writes to: a stub must be able to fetch the
    engine, and the engine's url and digest live in that registry's own `engine` block. So a
    published engine is picked up automatically and stubs never need rebuilding when it changes.
    """
    from types import SimpleNamespace

    from agent_runtime.infrastructure.products.factory import build_product_service

    config = SimpleNamespace(
        registry_url=f"{store.base_url}/index.json",
        engine_installer_url=os.environ.get("AGENTD_ENGINE_URL", ""),
        engine_installer_sha256=os.environ.get("AGENTD_ENGINE_SHA256", ""),
        engine_installer_platform=os.environ.get("AGENTD_ENGINE_PLATFORM", "win"),
        engine_version=os.environ.get("AGENTD_ENGINE_VERSION", ""),
        engine_min_version=os.environ.get("AGENTD_ENGINE_MIN_VERSION", ""),
        makensis_path=os.environ.get("MAKENSIS_PATH", ""),
        # A product built here is HOSTED: it signs into the same platform this service belongs to.
        distribution=SimpleNamespace(
            accounts_url=os.environ.get("PRODUCT_ACCOUNTS_URL", ""),
            model_proxy_url=os.environ.get("PRODUCT_MODEL_PROXY_URL", ""),
        ),
        state_dir="/tmp",  # noqa: S108 — the only writable path in a Lambda
    )
    return build_product_service(config)


_SERVICE = None


def service():
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = build_service()
    return _SERVICE


# ────────────────────────────── HTTP ──────────────────────────────


def handler(event, context=None):  # noqa: ARG001 — Lambda signature
    """ALB / API Gateway proxy event -> response dict."""
    from agent_runtime.application.interfaces.publish_intake import (
        BAD_REQUEST,
        SERVER_ERROR,
        Submission,
    )

    path = _path(event)
    if not path.endswith(PUBLISH_ROUTE):
        return _json(404, {"message": f"no route {path}"})
    if _method(event) != "POST":
        return _json(405, {"message": "POST a multipart form with a `package` file"})

    try:
        fields, files, filenames = _parse_multipart(event)
    except ValueError as e:
        return _json(BAD_REQUEST, {"message": str(e)})

    package = files.get("package") or files.get("bundle") or b""
    submission = Submission(
        package=package,
        token=_bearer(event),
        bundle_id=fields.get("bundle_id", ""),
        # The PACKAGE part's own name — never "whichever file part came last".
        filename=(
            fields.get("filename", "")
            or filenames.get("package", "")
            or filenames.get("bundle", "")
        ),
    )
    try:
        result = service().submit(submission)
    except TimeoutError as e:
        # The index lock. A retryable, explainable condition — not a 500.
        return _json(503, {"message": str(e)})
    except Exception:  # noqa: BLE001 — never leak a traceback to a caller
        log.exception("publish failed")
        return _json(SERVER_ERROR, {"message": "the publish service failed. Nothing was published."})
    return _json(result.status, result.body())


def _json(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "statusDescription": f"{status} ",
        "isBase64Encoded": False,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _path(event) -> str:
    return str(event.get("path") or event.get("rawPath") or "")


def _method(event) -> str:
    context = event.get("requestContext") or {}
    http = context.get("http") or {}
    return str(event.get("httpMethod") or http.get("method") or "").upper()


def _headers(event) -> dict:
    raw = event.get("headers") or {}
    return {str(k).lower(): v for k, v in raw.items()}


def _bearer(event) -> str:
    value = str(_headers(event).get("authorization") or "")
    return value.split(" ", 1)[1].strip() if value.lower().startswith("bearer ") else value.strip()


def _parse_multipart(event) -> tuple[dict, dict, dict]:
    """-> (text fields, file bytes by field, file NAMES by field). Uses the stdlib email parser
    rather than a dependency.

    The body arrives base64-encoded whenever it is binary, which a .agentpkg always is; decoding it
    as text would corrupt the zip in a way that only shows up as "not a valid .agentpkg".

    FILENAMES ARE KEYED BY FIELD, which is the whole reason this returns three dicts. They used to
    share one `__package_name__` slot, so with more than one file part the LAST one won — and the
    client posts `package` then `installer`, so the package inherited the installer's name. It
    reached the payload as `bundles/<id>-<ver>-setup.exe`, which the engine (globbing `*.agentpkg`)
    cannot see: an app that installs, opens, and has no agent in it.
    """
    import base64
    import email
    from email.policy import HTTP

    headers = _headers(event)
    content_type = str(headers.get("content-type") or "")
    if "multipart/form-data" not in content_type.lower():
        raise ValueError("expected a multipart/form-data body containing a `package` file")

    body = event.get("body") or ""
    raw = base64.b64decode(body) if event.get("isBase64Encoded") else str(body).encode("utf-8", "surrogateescape")

    message = email.message_from_bytes(
        b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw,
        policy=HTTP,
    )
    fields: dict[str, str] = {}
    files: dict[str, bytes] = {}
    filenames: dict[str, str] = {}
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        name = part.get_param("name", header="content-disposition") or ""
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[name] = payload
            filenames[name] = filename
        else:
            fields[name] = payload.decode("utf-8", "replace").strip()
    if not files:
        raise ValueError("the multipart body contained no file part named `package`")
    return fields, files, filenames
