"""Live local dashboard — what CloudWatch will show you, on your laptop, right now.

The services print metrics as JSON lines (EMF). On AWS the awslogs driver forwards those to
CloudWatch, which draws the graphs. Locally there is no CloudWatch, so this script reads the
exact same lines and draws the same numbers in your terminal — the feedback loop, without a
deploy.

    # terminal 1 — tell every service to also append metrics to a file, then run the stack
    $env:AGENTD_TELEMETRY_FILE = "$PWD\.telemetry.jsonl"
    python deploy/dev.py --model-proxy

    # terminal 2
    python monitoring/dev_dashboard.py

dev.py copies its own environment into every child it spawns, so setting the variable in the
shell before launching is enough — no change to dev.py required.

This is a DEV TOOL. It is not deployed, not imported by anything, and has no dependencies.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

WINDOW_SECONDS = 300  # rolling window; matches the 5-minute period most alarms will use
REDRAW_SECONDS = 1.0

C_RESET, C_DIM, C_BOLD = "\033[0m", "\033[2m", "\033[1m"
C_RED, C_GREEN, C_YELLOW, C_CYAN = "\033[31m", "\033[32m", "\033[33m", "\033[36m"


def _init_console() -> tuple[str, str]:
    """Force UTF-8 out, and fall back to ASCII bars if the console still can't take it.

    Windows consoles default to cp1252, which cannot encode block-drawing characters — the same
    trap deploy/dev.py works around when pumping child output. Reconfigure first; if that fails
    (a redirected pipe, an odd terminal), degrade the glyphs rather than the tool.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    try:
        "█░".encode(sys.stdout.encoding or "utf-8")
        return "█", "░"
    except (UnicodeEncodeError, LookupError):
        return "#", "."


BAR_FULL, BAR_EMPTY = _init_console()


def _default_file() -> Path:
    env = os.environ.get("AGENTD_TELEMETRY_FILE", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / ".telemetry.jsonl"


class Window:
    """Rolling aggregation. Deliberately dumb: this mirrors what CloudWatch does to the same
    lines, so what you see here is what you will see there."""

    def __init__(self) -> None:
        self.counts: dict[tuple, deque] = defaultdict(deque)
        self.values: dict[str, deque] = defaultdict(deque)
        self.recent_fail: deque = deque(maxlen=8)

    def add(self, record: dict) -> None:
        aws = record.get("_aws") or {}
        blocks = aws.get("CloudWatchMetrics") or []
        if not blocks:
            return
        ts = aws.get("Timestamp", 0) / 1000.0
        for metric in blocks[0].get("Metrics", []):
            name = metric.get("Name")
            value = record.get(name)
            if name is None or value is None:
                continue
            outcome = record.get("outcome") or record.get("direction") or record.get("credential") or ""
            self.counts[(name, outcome)].append((ts, float(value)))
            self.values[name].append((ts, float(value)))
            if outcome in ("fail", "error", "rejected", "unavailable", "unreachable"):
                self.recent_fail.append(
                    (ts, name, record.get("reason") or outcome, record.get("account_id") or "-")
                )

    def prune(self, now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        for series in (*self.counts.values(), *self.values.values()):
            while series and series[0][0] < cutoff:
                series.popleft()

    def total(self, name: str, outcome: str | None = None) -> float:
        if outcome is None:
            return sum(v for _, v in self.values.get(name, ()))
        return sum(v for _, v in self.counts.get((name, outcome), ()))

    def pct(self, name: str, q: float) -> float | None:
        pts = sorted(v for _, v in self.values.get(name, ()))
        if not pts:
            return None
        return pts[min(int(len(pts) * q), len(pts) - 1)]


def _bar(ok: float, bad: float, width: int = 24) -> str:
    total = ok + bad
    if total <= 0:
        return C_DIM + BAR_EMPTY * width + C_RESET
    bad_cells = min(width, round(width * bad / total))
    if bad and bad_cells == 0:
        bad_cells = 1  # never render a real failure as zero width
    return C_GREEN + BAR_FULL * (width - bad_cells) + C_RED + BAR_FULL * bad_cells + C_RESET


def _ms(v: float | None) -> str:
    return "   --" if v is None else f"{v:6.0f}ms"


def render(w: Window, path: Path, lines_seen: int) -> str:
    now = time.time()
    w.prune(now)
    out: list[str] = []
    a = out.append

    a(f"{C_BOLD}agentd — live metrics{C_RESET}  {C_DIM}last {WINDOW_SECONDS//60} min · "
      f"{lines_seen} lines · {path.name} · {time.strftime('%H:%M:%S')}{C_RESET}")
    a("")

    # ---- MONEY (the section with a failure mode that has no other symptom) ----
    spend = w.total("model_cost_usd")
    unbilled = w.total("unbilled_cost_usd")
    led_ok = w.total("ledger_write_total", "ok")
    led_fail = w.total("ledger_write_total", "fail")
    led_skip = w.total("ledger_write_total", "skipped")
    a(f"{C_BOLD}MONEY{C_RESET}")
    a(f"  spend                 ${spend:8.4f}")
    if unbilled > 0:
        a(f"  {C_RED}UNBILLED SPEND        ${unbilled:8.4f}   <-- money out, nothing recorded{C_RESET}")
    ledger_colour = C_RED if led_fail else C_GREEN
    a(f"  ledger writes         {ledger_colour}{led_ok:.0f} ok  {led_fail:.0f} fail{C_RESET}"
      f"  {C_DIM}{led_skip:.0f} skipped{C_RESET}  {_bar(led_ok, led_fail)}")
    a("")

    # ---- TRAFFIC ----
    calls_ok = w.total("model_call_total", "ok")
    tok_in = w.total("tokens_total", "in")
    tok_out = w.total("tokens_total", "out")
    a(f"{C_BOLD}TRAFFIC{C_RESET}")
    a(f"  model calls           {calls_ok:.0f}")
    a(f"  tokens                {tok_in:,.0f} in   {tok_out:,.0f} out")
    a("")

    # ---- AUTH (sits in front of every model call) ----
    auth_ok = w.total("auth_total", "ok")
    auth_bad = (w.total("auth_total", "rejected") + w.total("auth_total", "unavailable")
                + w.total("auth_total", "none"))
    a(f"{C_BOLD}AUTH{C_RESET}")
    a(f"  outcomes              {auth_ok:.0f} ok  "
      f"{C_RED if auth_bad else C_DIM}{auth_bad:.0f} refused{C_RESET}  {_bar(auth_ok, auth_bad)}")
    p50, p95 = w.pct("resolve_latency_ms", 0.50), w.pct("resolve_latency_ms", 0.95)
    warn = C_RED if (p95 or 0) > 1000 else C_RESET
    a(f"  resolve latency       p50 {_ms(p50)}   {warn}p95 {_ms(p95)}{C_RESET}"
      f"   {C_DIM}(blocks every model call){C_RESET}")
    a("")

    # ---- RECENT FAILURES ----
    a(f"{C_BOLD}RECENT FAILURES{C_RESET}")
    if not w.recent_fail:
        a(f"  {C_DIM}none{C_RESET}")
    else:
        for ts, name, reason, acct in list(w.recent_fail)[-6:]:
            a(f"  {C_DIM}{time.strftime('%H:%M:%S', time.localtime(ts))}{C_RESET}  "
              f"{C_YELLOW}{name:<22}{C_RESET} {reason:<20} {C_DIM}{acct}{C_RESET}")
    a("")
    a(f"{C_DIM}Ctrl+C to stop. Same lines CloudWatch will read in production.{C_RESET}")
    return "\n".join(out)


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_file()
    print(f"watching {path}")
    while not path.exists():
        print(f"  waiting for {path.name} … (start the stack with AGENTD_TELEMETRY_FILE set)")
        time.sleep(2)

    window, seen = Window(), 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        fh.seek(0, os.SEEK_END)  # only what happens from now on
        last = 0.0
        while True:
            line = fh.readline()
            if line:
                try:
                    window.add(json.loads(line))
                    seen += 1
                except (json.JSONDecodeError, AttributeError, TypeError):
                    pass  # a non-EMF line (plain log) — not ours
                continue
            now = time.time()
            if now - last >= REDRAW_SECONDS:
                last = now
                sys.stdout.write("\033[2J\033[H" + render(window, path, seen) + "\n")
                sys.stdout.flush()
            time.sleep(0.1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
