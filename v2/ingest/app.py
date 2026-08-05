"""Ingest — the mail slot for telemetry from machines we do not own (plan item 5.2).

WHY THIS SERVICE EXISTS, AND WHY IT IS THE ONLY ONE IN THE PLAN. A program's output lands where
the program runs. Accounts and the Model Proxy run on AWS, so their `print()` reaches CloudWatch
for free via the awslogs driver — which is why Phases 0-3 needed no service at all. But in
desktop Cloud mode the DAEMON runs on the user's PC, and the browser client runs in their tab.
Those two produce the only signals we cannot otherwise see — how long a run actually took on
their hardware, whether the daemon even started, whether the WebSocket keeps dropping — and the
lines land on their disk where we can never read them. So the app mails us a summary, and
something has to receive it.

WHAT IT DOES: accepts a batch of events, drops everything not on an allowlist, and prints the
survivors as EMF. That is the whole service. It writes to no database, calls nothing downstream,
and holds no state beyond a rate-limit counter — so the blast radius of it being wrong, slow, or
compromised is "some telemetry is missing", never "a user cannot work".

THE THREAT MODEL IS THE POINT. Every other telemetry producer in this system is code we deployed.
This input arrives from a machine the user controls, so it is hostile by construction:

  * A METRIC NAME IS A BILLED RESOURCE. CloudWatch charges per metric per month; a client that
    can choose the name can invent thousands and bill us for them forever. Names come from a
    fixed allowlist, and unknown ones are counted and dropped rather than published.
  * SO IS A DIMENSION VALUE. Same reasoning, worse: dimensions multiply. Client-supplied values
    are only allowed for keys with a fixed vocabulary, checked against it.
  * FIELDS ARE ALLOWLISTED TWICE. The uploader already scrubs with `redact.ALLOWED` before
    sending, but that runs on the user's machine and can be replaced. This side re-checks
    everything, so no amount of tampering with the client puts message text in our logs.
  * NUMBERS ARE CLAMPED. An unbounded `duration_ms` poisons every percentile it lands in; an
    unbounded value is also a way to make a graph useless without ever tripping an alarm.
  * `account_id` IS TAKEN FROM THE TOKEN, NEVER THE BODY, when a token is presented — otherwise
    one user attributes their traffic to another and the anomaly signals become unusable.

None of this stops a determined user from sending PLAUSIBLE lies about their own runs. It cannot:
the data is self-reported by definition. What it does stop is a client turning our telemetry into
a bill, a data leak, or another account's problem.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

# Shared instrumentation. Optional at import, like every other service: telemetry must never be
# the reason a process fails to boot -- least of all the telemetry service.
try:
    from agentd_telemetry import count, setup_logging, timing
    from agentd_telemetry.redact import ALLOWED as ALLOWED_FIELDS
    from agentd_telemetry.redact import scrub
except ImportError:  # pragma: no cover
    ALLOWED_FIELDS = set()

    def count(*_a, **_k):  # type: ignore[misc]
        pass

    def timing(*_a, **_k):  # type: ignore[misc]
        pass

    def setup_logging(*_a, **_k):  # type: ignore[misc]
        pass

    def scrub(fields: dict) -> dict:  # type: ignore[misc]
        return {}


setup_logging("ingest")

app = FastAPI(title="agentd ingest", version="1")

# The browser client posts here cross-origin, so CORS is load-bearing rather than incidental.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip() for o in os.environ.get("INGEST_CORS_ORIGINS", "*").split(",") if o.strip()
    ],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)

# ─────────────────────────── the allowlist ───────────────────────────
# WHAT MAY BE PUBLISHED, and as what. Adding a metric is one entry here plus one on the client;
# both sides must agree or the event is dropped, which is the intended failure mode for a schema
# that drifted. `unit` matches the telemetry library's vocabulary; `max` clamps the value.
#
# Keep this SHORT. Each name is a CloudWatch custom metric at $0.30/month per dimension set, and
# these are emitted under {service} and {service,outcome} rollups like everything else.

_MS = ("Milliseconds", 3_600_000.0)  # an hour: longer is a stuck client, not a slow run
_COUNT = ("Count", 100_000.0)

ALLOWED_METRICS: dict[str, tuple[str, float]] = {
    # --- desktop daemon: the run, as the USER experienced it -------------------
    # These exist because the proxy can only see its own slice. It knows a model call took 4s;
    # it cannot know the user waited 30s because a tool ran on a cold disk.
    "client_run_ms": _MS,
    "client_model_ms": _MS,
    "client_tool_ms": _MS,
    "client_first_output_ms": _MS,
    "client_turns": _COUNT,
    "client_run_total": _COUNT,
    # --- desktop shell: does the product even start? --------------------------
    # v0.1.0 shipped a broken embedded runtime and we found out from a user, because a daemon
    # that never starts produces no traffic anywhere -- absence, which nothing was watching.
    "client_daemon_start_total": _COUNT,
    "client_daemon_start_ms": _MS,
    "client_connect_total": _COUNT,
    # --- browser (RUM) --------------------------------------------------------
    "web_page_load_ms": _MS,
    "web_first_token_ms": _MS,
    "web_error_total": _COUNT,
    "web_ws_reconnect_total": _COUNT,
    "web_run_total": _COUNT,
}

#: Dimension keys a client may set, and the ONLY values each may take. A dimension is a billed
#: axis, so this is a fixed vocabulary rather than a type check — `outcome=<anything>` would let
#: one client mint unlimited metrics under a name that is itself on the allowlist.
ALLOWED_DIMENSIONS: dict[str, frozenset[str]] = {
    "outcome": frozenset({"ok", "error", "aborted", "refused", "timeout"}),
    "surface": frozenset({"desktop", "web"}),
    "reason": frozenset(
        {"crash", "timeout", "runtime_missing", "port_in_use", "auth", "network", "unknown"}
    ),
}

#: Hard caps. A batch is a convenience for the client, not an invitation.
MAX_EVENTS_PER_BATCH = 100
MAX_PROPS_PER_EVENT = 20


def _limits() -> tuple[int, float]:
    raw = os.environ.get("INGEST_RATE_LIMIT", "60/60")
    try:
        n, per = raw.split("/", 1)
        return int(n), float(per)
    except (ValueError, AttributeError):
        return 60, 60.0


_hits: dict[str, tuple[int, float]] = {}


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _check_rate(request: Request) -> None:
    """Per-IP fixed window. Crude on purpose: the failure this prevents is one machine in a
    reboot loop spending our CloudWatch budget, and a crude counter stops that just as well as a
    token bucket while being auditable at a glance."""
    limit, per = _limits()
    if limit <= 0 or per <= 0:
        return
    ip, now = _client_ip(request), time.time()
    hits, start = _hits.get(ip, (0, now))
    if now - start >= per:
        hits, start = 0, now
    hits += 1
    _hits[ip] = (hits, start)
    if len(_hits) > 10_000:  # bound memory under address churn
        _hits.clear()
    if hits > limit:
        count("ingest_rejected_total", reason="rate_limit")
        raise HTTPException(status_code=429, detail="too many events; slow down")


# ─────────────────────────── identity ───────────────────────────

_ACCOUNTS_URL = (os.environ.get("ACCOUNTS_URL", "") or "").rstrip("/")
_RESOLVE_TTL = float(os.environ.get("INGEST_RESOLVE_TTL_SECONDS", "300") or 300)
_resolved: dict[str, tuple[str, float]] = {}


def _account_for(authorization: str | None) -> str:
    """Resolve a session token to an account id, or '' for anonymous.

    ANONYMOUS IS ALLOWED, DELIBERATELY. The single most valuable event here is "the daemon failed
    to start" — and a client that cannot start often cannot sign in either. Refusing unauthenticated
    events would blind us to exactly the failure this endpoint exists for. Unattributed events are
    still rate-limited and still allowlisted; they just carry no account_id.

    Cached, because a client batching every 30s must not add a /resolve call to the accounts
    service's load — that endpoint is already the platform's hot path (DEF-6).
    """
    if not authorization or not authorization.startswith("Bearer ") or not _ACCOUNTS_URL:
        return ""
    token = authorization[len("Bearer ") :].strip()
    if not token:
        return ""
    hit = _resolved.get(token)
    now = time.time()
    if hit and now - hit[1] < _RESOLVE_TTL:
        return hit[0]
    try:
        req = urllib.request.Request(
            f"{_ACCOUNTS_URL}/resolve", headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            import json as _json

            account_id = str(_json.loads(r.read() or b"{}").get("account_id") or "")
    except (urllib.error.URLError, ValueError, TimeoutError):
        # Fail OPEN to anonymous, never 500. Accounts being slow must not cost us the telemetry
        # that would tell us accounts is slow.
        count("ingest_resolve_total", outcome="error")
        return ""
    if len(_resolved) > 5_000:
        _resolved.clear()
    _resolved[token] = (account_id, now)
    count("ingest_resolve_total", outcome="ok")
    return account_id


# ─────────────────────────── the endpoint ───────────────────────────


def _clean_event(event: Any, account_id: str) -> tuple[str, float, str, dict, dict] | None:
    """Validate one event down to something publishable, or None."""
    if not isinstance(event, dict):
        return None
    name = str(event.get("name") or "")
    spec = ALLOWED_METRICS.get(name)
    if spec is None:
        count("ingest_rejected_total", reason="unknown_metric", _props={"event": name[:60]})
        return None
    unit, ceiling = spec
    try:
        value = float(event.get("value", 1))
    except (TypeError, ValueError):
        count("ingest_rejected_total", reason="bad_value", _props={"event": name})
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        count("ingest_rejected_total", reason="bad_value", _props={"event": name})
        return None
    # Clamped, not rejected: a plausible-but-huge duration is still evidence something hung, and
    # dropping it would hide the incident. An unclamped one poisons the percentile instead.
    value = max(0.0, min(value, ceiling))

    dims: dict[str, str] = {}
    for key, allowed in ALLOWED_DIMENSIONS.items():
        raw = event.get(key)
        if raw is None:
            continue
        text = str(raw)
        dims[key] = text if text in allowed else "other"

    # Properties: allowlisted by the SAME set the library uses, then scrubbed and truncated by
    # the same function. One definition of "safe field", not two that drift.
    raw_props = event.get("props")
    props: dict = {}
    if isinstance(raw_props, dict):
        for key, val in list(raw_props.items())[:MAX_PROPS_PER_EVENT]:
            if key in ALLOWED_FIELDS:
                props[key] = val
    props = scrub(props)
    # The token wins over anything the body claims. Without this, one client attributes its
    # traffic to another account and every per-account signal becomes untrustworthy.
    props.pop("account_id", None)
    if account_id:
        props["account_id"] = account_id
    return name, value, unit, dims, props


@app.post("/v1/events")
def receive(
    request: Request,
    payload: dict = Body(...),
    authorization: str | None = Header(default=None),
) -> dict:
    """Accept a batch. Always 200 unless rate-limited or malformed at the envelope level.

    PARTIAL ACCEPTANCE IS THE CONTRACT. One bad event must not reject the batch, because the
    client's only sane response to a 4xx is to drop the whole buffer — losing the good events
    with the bad one. The response reports what was taken and what was dropped so a client can
    log the discrepancy, and `ingest_rejected_total{reason}` makes a client that has drifted out
    of schema visible from our side rather than only theirs.
    """
    _check_rate(request)
    events = payload.get("events")
    if not isinstance(events, list):
        raise HTTPException(status_code=400, detail="events must be a list")
    if len(events) > MAX_EVENTS_PER_BATCH:
        count("ingest_rejected_total", reason="batch_too_large")
        raise HTTPException(status_code=413, detail=f"at most {MAX_EVENTS_PER_BATCH} events")

    account_id = _account_for(authorization)
    surface = str(payload.get("surface") or "")
    accepted = 0
    for event in events:
        if surface and "surface" not in event and isinstance(event, dict):
            event = {**event, "surface": surface}
        cleaned = _clean_event(event, account_id)
        if cleaned is None:
            continue
        name, value, unit, dims, props = cleaned
        if unit == "Milliseconds":
            timing(name, value, _props=props, **dims)
        else:
            count(name, value, _props=props, **dims)
        accepted += 1

    dropped = len(events) - accepted
    # The client also reports what its own buffer threw away, so a laptop that was offline for a
    # day is visible as loss rather than as a quiet gap in the graphs.
    try:
        overflow = int(payload.get("dropped") or 0)
    except (TypeError, ValueError):
        overflow = 0
    if overflow > 0:
        count("client_buffer_dropped_total", min(overflow, 1_000_000),
              surface=surface if surface in ALLOWED_DIMENSIONS["surface"] else "other")
    count("ingest_batch_total", outcome="ok")
    count("ingest_events_total", accepted)
    return {"ok": True, "accepted": accepted, "dropped": dropped}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "ingest"}


@app.get("/health/ready")
def ready() -> dict:
    """Readiness is the same as liveness here, and that is worth stating rather than omitting:
    this service has no database and no downstream it needs in order to do its job. Accounts
    being unreachable degrades events to anonymous; it does not make ingest unready."""
    return {"ok": True, "accounts": "optional"}


@app.get("/v1/schema")
def schema() -> dict:
    """What this deployment will accept. A client can check its own allowlist against a running
    service instead of discovering a mismatch as silently-dropped events weeks later."""
    return {
        "metrics": {name: {"unit": u, "max": m} for name, (u, m) in ALLOWED_METRICS.items()},
        "dimensions": {k: sorted(v) for k, v in ALLOWED_DIMENSIONS.items()},
        "fields": sorted(ALLOWED_FIELDS),
        "max_events_per_batch": MAX_EVENTS_PER_BATCH,
    }
