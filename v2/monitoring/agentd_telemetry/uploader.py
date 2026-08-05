"""The mail path — forwarding a few metrics off a machine we do not own (plan item 5.1).

WHY THIS EXISTS. Everything else in this library works by printing: on AWS the awslogs driver
carries stdout to CloudWatch and nothing else is needed. In desktop Cloud mode the daemon runs on
the USER's PC, where its stdout lands on their disk and we can never read it. So the handful of
signals that only exist there — how long a run took on their hardware, whether the daemon even
started — have to be mailed to `v2/ingest`.

FOUR PROPERTIES, EACH OF WHICH IS A DECISION.

1. OPT-IN, DEFAULT OFF. No URL configured or the toggle off means this module never opens a
   socket. It is telemetry from a private machine; consent is the default state, not a setting to
   find.

2. IT FORWARDS A NAMED SHORTLIST, NOT EVERYTHING. `emit` sees every metric the daemon produces —
   dozens per run. Sending them all would be a bandwidth and CloudWatch bill for data we already
   have from the proxy, so `FORWARD` maps a small set of LOCAL names to the `client_*` names
   ingest publishes. Renaming on the way out is deliberate: `run_duration_ms` measured on a user's
   laptop and `run_duration_ms` measured in our own cloud daemon are different populations, and
   silently merging them into one graph would make both meaningless.

3. IT NEVER BLOCKS AND NEVER RAISES. Queuing is a bounded `deque.append` on the caller's thread —
   no lock contention, no I/O. A daemon thread does the HTTP. If the network is down, the buffer
   fills and the OLDEST events are discarded (a `maxlen` deque), because when you can only keep
   some of a run's history the recent part is the part that explains the current problem. What was
   dropped is counted and reported in the next batch, so a gap in the graphs is visible as loss
   rather than as "nothing happened".

4. THE ALLOWLIST APPLIES BEFORE IT LEAVES. `redact.scrub` has already run by the time a record
   reaches `emit`, and the receiving end allowlists again. Two independent gates, because this is
   the one path where a mistake ships user content off their machine.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from collections import deque

#: LOCAL metric name -> the name ingest publishes it under. Only these are ever forwarded.
#: Overridable with AGENTD_TELEMETRY_FORWARD ("local=remote,local=remote") so an environment can
#: narrow the list further without a release; unknown targets are simply dropped by ingest.
_DEFAULT_FORWARD = {
    "run_duration_ms": "client_run_ms",
    "model_time_ms": "client_model_ms",
    "tool_duration_ms": "client_tool_ms",
    "first_output_ms": "client_first_output_ms",
    "run_total": "client_run_total",
    # The daemon-start signals: v0.1.0 shipped a broken embedded runtime and produced NO traffic
    # at all, so the failure was invisible from our side until a user said their PC froze.
    "daemon_start_total": "client_daemon_start_total",
    "daemon_start_ms": "client_daemon_start_ms",
    "platform_connect_total": "client_connect_total",
}


def _forward_map() -> dict[str, str]:
    raw = os.environ.get("AGENTD_TELEMETRY_FORWARD", "").strip()
    if not raw:
        return dict(_DEFAULT_FORWARD)
    pairs = {}
    for item in raw.split(","):
        if "=" in item:
            local, remote = item.split("=", 1)
            if local.strip() and remote.strip():
                pairs[local.strip()] = remote.strip()
    return pairs or dict(_DEFAULT_FORWARD)


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class Uploader:
    """One batching sender. Constructed once at import; inert until configured AND enabled."""

    def __init__(self) -> None:
        self._url = (os.environ.get("AGENTD_TELEMETRY_UPLOAD_URL", "") or "").rstrip("/")
        self._enabled = _flag("AGENTD_TELEMETRY_UPLOAD", False)
        self._surface = os.environ.get("AGENTD_TELEMETRY_SURFACE", "desktop").strip() or "desktop"
        self._interval = max(5.0, float(_int("AGENTD_TELEMETRY_UPLOAD_INTERVAL", 30)))
        self._forward = _forward_map()
        # Bounded. ~2 KB an event, so 500 is about a megabyte at absolute worst — small enough
        # that an offline laptop cannot grow the process, large enough to survive a lunch break.
        self._queue: deque[dict] = deque(maxlen=_int("AGENTD_TELEMETRY_BUFFER", 500))
        self._dropped = 0
        self._token = ""
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()

    # --- configuration ---------------------------------------------------
    @property
    def active(self) -> bool:
        return bool(self._enabled and self._url)

    def configure(self, *, enabled: bool | None = None, url: str | None = None,
                  token: str | None = None, surface: str | None = None) -> None:
        """Change the settings at RUNTIME.

        Needed because both halves arrive late: the user flips the toggle while the daemon is
        running, and the session token only exists after sign-in. A restart-only switch would mean
        the first run after enabling — usually the one you turned it on to investigate — is the
        one that goes unreported.
        """
        with self._lock:
            if enabled is not None:
                self._enabled = bool(enabled)
            if url is not None:
                self._url = url.rstrip("/")
            if token is not None:
                self._token = token
            if surface is not None and surface.strip():
                self._surface = surface.strip()
            if not self.active:
                # Turning it OFF discards what was queued. Keeping it would mean re-enabling later
                # ships events recorded while the user had said no.
                self._queue.clear()
                self._dropped = 0
        if self.active:
            self._ensure_thread()

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="agentd-telemetry-upload",
                                        daemon=True)
        self._thread.start()

    # --- the hot path ----------------------------------------------------
    def offer(self, record: dict) -> None:
        """Called from `emf.emit` for EVERY record. Must be nearly free and must never raise."""
        if not self._enabled or not self._url:
            return
        try:
            aws = record.get("_aws")
            if not aws:
                return  # a log line, not a metric
            metrics = aws["CloudWatchMetrics"][0]["Metrics"]
            name = metrics[0]["Name"]
            remote = self._forward.get(name)
            if remote is None:
                return
            event = {"name": remote, "value": record.get(name, 1)}
            for key in ("outcome", "reason"):
                if record.get(key) is not None:
                    event[key] = record[key]
            # Correlation only. Everything on the record already passed redact.scrub, and the
            # receiver allowlists again; naming the fields here keeps the payload to what the
            # graph actually needs rather than whatever a call site happened to bind.
            props = {k: record[k] for k in ("run_id", "agent_id", "trigger", "tool")
                     if record.get(k) is not None}
            if props:
                event["props"] = props
            with self._lock:
                if len(self._queue) == self._queue.maxlen:
                    self._dropped += 1  # deque.append will evict the oldest
                self._queue.append(event)
                ready = len(self._queue) >= 50
            if ready:
                self._wake.set()
        except Exception:  # noqa: BLE001 — telemetry may never break the caller
            pass

    # --- the sender ------------------------------------------------------
    def _loop(self) -> None:
        while True:
            # Woken early by a full-ish buffer; otherwise a plain interval. Either way the thread
            # is asleep, not spinning.
            self._wake.wait(timeout=self._interval)
            self._wake.clear()
            if not self.active:
                continue
            try:
                self.flush()
            except Exception:  # noqa: BLE001 — a sender that dies stops all telemetry silently
                pass

    def flush(self) -> int:
        """Send everything queued. Returns how many events were accepted for sending.

        ON FAILURE THE BATCH IS DISCARDED, not retried. Deliberate: these are metrics, not
        billing rows, and a retry loop against a broken endpoint turns a dead ingest service into
        a client-side memory leak plus a thundering herd when it recovers. The loss shows up as
        `dropped` on the next successful batch.
        """
        with self._lock:
            if not self._queue:
                return 0
            events = list(self._queue)
            dropped = self._dropped
            self._queue.clear()
            self._dropped = 0
            url, token, surface = self._url, self._token, self._surface

        body = json.dumps({"surface": surface, "events": events, "dropped": dropped}).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(f"{url}/v1/events", data=body, method="POST",
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10):
                return len(events)
        except (urllib.error.URLError, TimeoutError, OSError):
            return 0

    # --- introspection, for the settings UI and tests --------------------
    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": bool(self._enabled),
                "url": self._url,
                "active": self.active,
                "queued": len(self._queue),
                "dropped": self._dropped,
                "surface": self._surface,
                "identified": bool(self._token),
                "forwards": sorted(self._forward),
            }


#: The process-wide instance. `emf.emit` offers to this; everything else configures it.
uploader = Uploader()


def configure(**kwargs) -> None:
    """Public entry point — see `Uploader.configure`."""
    uploader.configure(**kwargs)


def status() -> dict:
    return uploader.status()


def flush() -> int:
    """Send now. Called on daemon shutdown so the last run of a session is not lost."""
    return uploader.flush()
