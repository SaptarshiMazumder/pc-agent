"""The scheduler's hand: POST one accounts-service endpoint, then get out of the way.

WHY THIS FUNCTION EXISTS AT ALL. Three accounts endpoints must run on a clock rather than on a
user action -- balance-sheet levels have no event to hang off, and "renew Sally on the 3rd" is
nobody's request. EventBridge Scheduler is the clock, but Scheduler cannot call an HTTP endpoint
directly (its universal targets are AWS APIs; API destinations belong to EventBridge Rules and
require HTTPS, while this ALB is HTTP-only for the private-testing phase). So the smallest
possible Lambda stands between the clock and the endpoint.

WHAT IT DELIBERATELY DOES NOT DO.
  * No business logic. Which endpoint to call arrives in the event payload, so adding a job is a
    Terraform data change (var.scheduled_jobs) and never a code change here.
  * No retry loop. Scheduler's own retry_policy owns that, and every target endpoint is
    idempotent -- which is the only reason retrying is safe.
  * No custom metrics. Failure shows up in the function's own AWS/Lambda `Errors` metric, which
    is vendor-supplied and therefore FREE; a custom EMF counter would cost $0.30/metric/month to
    say the same thing. An unhandled exception here is the signal, so errors are re-raised.
  * No third-party dependencies. urllib + boto3 (already in the runtime) means no build step and
    no zip to keep in sync.

NETWORK / CREDENTIAL SHAPE. The function runs INSIDE the VPC and reaches accounts by its
service-discovery name, so the internal key -- which can read the whole ledger and mint credits
-- never crosses the public internet in cleartext. The cost of that choice is that the function
has no internet route (public subnets, no NAT), so the key cannot be fetched from Secrets Manager
without a paid VPC endpoint; it arrives as an encrypted-at-rest environment variable instead.
That trade is deliberate: reading it requires an IAM caller with lambda:GetFunctionConfiguration,
whereas cleartext-over-internet requires only a listener.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

_BASE = os.environ["ACCOUNTS_URL"].rstrip("/")
_KEY = os.environ["ACCOUNTS_INTERNAL_KEY"]
_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "45"))


def handler(event, context):  # noqa: ANN001, ANN201 - AWS Lambda entry point
    """Invoke one job. `event` is the Scheduler input: {"job": "...", "path": "/..."}."""
    event = event or {}
    job = str(event.get("job") or "?")
    path = str(event.get("path") or "")
    # A relative path only. Refusing anything else keeps a mistyped schedule from turning this
    # function -- which holds a credential that can read the whole ledger and mint credits --
    # into a POST relay to an arbitrary host. "//host/x" is rejected explicitly: it passes a
    # naive startswith("/") and is the protocol-relative form of an absolute URL.
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError(f"job {job}: 'path' must start with '/', got {path!r}")

    request = urllib.request.Request(
        _BASE + path,
        data=b"",  # every one of these endpoints is a POST with no body
        method="POST",
        headers={"X-Internal-Key": _KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            status = response.status
            body = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        # Log the service's own reason before failing -- a 401 (rotated key) and a 500 (broken
        # ledger) both surface as one Errors datapoint, and only the body tells them apart.
        # Reading the body is itself allowed to fail (an error with no readable stream), and if
        # it does the diagnostic must degrade to the status line rather than replace the real
        # failure with an error from the logging path.
        try:
            raw = e.read() or b""
            detail = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        except Exception:  # noqa: BLE001
            detail = str(e.reason or "")
        print(json.dumps({"job": job, "path": path, "outcome": "http_error",
                          "status": e.code, "detail": detail[:500]}))
        raise
    except Exception as e:  # noqa: BLE001 - DNS/connect/timeout; re-raised, never swallowed
        print(json.dumps({"job": job, "path": path, "outcome": "unreachable",
                          "error": type(e).__name__, "detail": str(e)[:500]}))
        raise

    # The result is the audit trail: `grants_closed`, `renewed`, `balanced` all land here, and
    # this log group is where you read what a scheduled run actually did.
    print(json.dumps({"job": job, "path": path, "outcome": "ok", "status": status,
                      "result": body}))
    return {"job": job, "path": path, "status": status, "result": body}
