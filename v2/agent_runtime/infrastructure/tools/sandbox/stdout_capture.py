"""Plugin stdout capture — the gap the redaction allowlist cannot close (plan item 5.4).

THE PROBLEM. `agentd_telemetry.redact` guarantees that no call *we* make can put user content into
a log line: every field passes an allowlist before it is printed. That guarantee covers our code.
It says nothing at all about a third-party plugin running IN OUR PROCESS that does

    print(f"calling {url} with key {api_key}")

Their `print` writes to the same `sys.stdout` as our EMF lines. On AWS the awslogs driver forwards
it verbatim to CloudWatch, where it is now durable, searchable, and outside every control we built.
The allowlist is not bypassed — it was never in that path.

Two distinct harms, and it is worth separating them because they need different answers:

  * LEAKAGE. A key, a token, or a user's file contents lands in our logs. Nothing downstream can
    un-log it, and the plugin author has no idea it happened.
  * CONTAMINATION. A plugin printing a line that happens to look like JSON with an `_aws` key
    would have CloudWatch extract a metric from it — a third party minting billed metrics in our
    namespace. Even innocently, a chatty plugin drowns the service's own stream.

WHAT THIS DOES. During a sandboxed tool call, stdout and stderr are redirected into a buffer. When
the call ends the buffer is scrubbed for obvious secrets, truncated, capped by line count, and
re-emitted as ONE structured log line tagged with the plugin that produced it. Nothing is silently
discarded: a plugin's debugging output is useful, and an author who cannot see their own `print`
resorts to worse things. It is just no longer indistinguishable from ours.

WHAT THIS IS NOT. It is not isolation. A plugin in the same process can still reach
`sys.__stdout__`, open a socket, or read the environment — the redirect is cooperative, and only
a real sandbox backend (container / microVM / remote) makes it enforced. This closes the ACCIDENT,
which is what actually happens; the deliberate case needs the blue backends behind the same port.

WHY AT THE SANDBOX BOUNDARY. It is the one place that already knows "this code is not ours",
carries the plugin id, and wraps exactly one call. Doing it in the tool executor would catch our
own built-ins too, and doing it globally would break the daemon's own stdout logging.

TWO CONSEQUENCES OF `redirect_stdout` BEING PROCESS-GLOBAL, both handled here rather than
discovered later:

  * OUR OWN METRICS WOULD BE SWALLOWED. `emf.emit` resolves `sys.stdout` per write, exactly like
    `print` does, so every metric emitted while a plugin tool ran would land in that plugin's
    buffer and never reach CloudWatch — telemetry vanishing precisely during third-party code.
    `emf.pin_stream` points our own output at the real stream for the duration. (Logging is
    already safe: `StreamHandler` binds its stream at construction.)
  * CONCURRENT PLUGIN CALLS SHARE ONE STDOUT. Two plugin tools running at once interleave into
    each other's buffers, so output can be attributed to the wrong plugin. That is a REPORTING
    defect, not a leak — the text is still captured, scrubbed and kept out of the raw stream —
    and it is not fixable cooperatively. A real (container / microVM / remote) backend gives each
    call its own process and its own file descriptors, which is the actual fix.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys

from agent_runtime.infrastructure import telemetry

#: Lines kept per tool call. A plugin in a print-loop must not be able to fill a log group.
MAX_LINES = int(os.environ.get("AGENTD_PLUGIN_STDOUT_LINES", "20") or 20)
#: Characters kept per line, and in total.
MAX_LINE_CHARS = 200
MAX_TOTAL_CHARS = 4_000

#: Shapes that are secrets wherever they appear. A DENYLIST is the wrong tool for deciding what
#: may be logged — which is why our own telemetry uses an allowlist — but this text is arbitrary
#: by definition, so there is nothing to allow-list against. It is a mitigation on output nobody
#: should have been printing, not a guarantee; the guarantee is that this text is tagged as the
#: plugin's and never merged into our metric stream.
_SECRET_PATTERNS = [
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{12,}", re.I),          # OpenAI/Stripe-style keys
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),                            # AWS access key id
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),                         # GitHub token
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),                # Slack token
    re.compile(r"\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),  # JWT
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token|authorization)\b\s*[:=]\s*\S+"),
]


def scrub_text(text: str) -> str:
    """Mask secret-shaped substrings and clamp the size."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    lines = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + "…"
        lines.append(line)
    dropped = max(0, len(lines) - MAX_LINES)
    kept = lines[:MAX_LINES]
    if dropped:
        kept.append(f"… {dropped} more line(s) suppressed")
    out = "\n".join(kept)
    return out[:MAX_TOTAL_CHARS]


class _Tee(io.TextIOBase):
    """Collects writes. Not a tee to the real stream, despite the name being tempting: passing
    through would defeat the whole point, since the raw line would still reach CloudWatch."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._size = 0

    def write(self, s: str) -> int:  # type: ignore[override]
        # Bounded during collection, not only at the end — a plugin that prints a gigabyte must
        # not be able to make us hold a gigabyte before we decide to throw it away.
        if self._size < MAX_TOTAL_CHARS * 4:
            self._parts.append(s)
            self._size += len(s)
        return len(s)

    def writable(self) -> bool:  # type: ignore[override]
        return True

    def value(self) -> str:
        return "".join(self._parts)


@contextlib.contextmanager
def capture(plugin_id: str, tool_name: str):
    """Redirect stdout/stderr for the duration of one plugin tool call.

    Re-emits whatever was printed as a single structured log line owned by `plugin_id`, and
    counts it — a plugin that suddenly starts printing is a change worth seeing, whether it is a
    new debug statement or a library dumping a stack trace on every call.
    """
    buffer = _Tee()
    # Keep OUR telemetry on the real stream while the plugin's is diverted. `previous` rather
    # than None on restore: a nested capture must not un-pin the outer one.
    previous = telemetry.pin_telemetry_stream(sys.stdout)
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            yield
    finally:
        telemetry.pin_telemetry_stream(previous)
        # In `finally`, so output a plugin produced before raising is not lost — that is usually
        # the output that explains the exception.
        raw = buffer.value()
        if raw.strip():
            text = scrub_text(raw)
            telemetry.count(
                "plugin_stdout_total",
                source="plugin",
                _props={"plugin_id": plugin_id, "tool": tool_name},
            )
            # Written through the LOGGER, not print: the logger's own formatter is what puts this
            # through our JSON envelope with the run's correlation ids attached. Printing it back
            # out would recreate exactly the problem this module exists to solve.
            _log_line(plugin_id, tool_name, text)


def _log_line(plugin_id: str, tool_name: str, text: str) -> None:
    import logging

    logging.getLogger("agentd.plugin.stdout").info(
        "plugin output captured",
        extra={
            "plugin_id": plugin_id,
            "tool": tool_name,
            "source": "plugin",
            # `message` is on the telemetry allowlist and truncated by it as well; this is the
            # second of the two gates, not the only one.
            "plugin_output": text,
        },
    )


def enabled() -> bool:
    """Off only by explicit request. The capture costs one context manager per plugin tool call
    and the leak it prevents is unrecoverable, so the default is on wherever the sandbox runs."""
    return (os.environ.get("AGENTD_PLUGIN_STDOUT_CAPTURE", "1") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def stdout_is_redirected() -> bool:
    """True while a capture is active. Exposed for tests, which otherwise cannot tell the
    difference between 'captured' and 'the plugin printed nothing'."""
    return not isinstance(sys.stdout, io.TextIOWrapper)
