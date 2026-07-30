"""One command to run the whole HOSTED stack locally — so you stop juggling terminals + env vars.

    python deploy/dev.py            # accounts + daemon + web   ->  open http://localhost:5273
    python deploy/dev.py --model-proxy  # ALSO run the Model Proxy (full hosted-key shape)
    python deploy/dev.py --no-web    # backends only (accounts + daemon)

Ctrl+C stops everything it started. Re-running is safe: it frees its own ports first.

What it starts (and why the proxy is OFF by default):
  * accounts service (:4100)  — identity + per-account budget/tab
  * agentd daemon    (:8790)  — accounts ON; calls providers DIRECTLY with your v2/.env keys
  * web client       (:5273)  — the browser UI, in accounts mode
The LiteLLM Model Proxy is only needed for the PRODUCTION property "provider keys live in a
separate process, not the daemon." For local dev that adds a 4th process for no functional gain —
accounts, metering, and per-user isolation all work without it — so it's opt-in via
--model-proxy (`--proxy` remains a short compatibility alias).

The DESKTOP app is separate and already one command:  cd clients/desktop && npm run dev
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

HERE = Path(__file__).resolve()
V2 = HERE.parents[1]
REPO = HERE.parents[2]
VENV_PY = (
    REPO
    / ".venv"
    / ("Scripts" if os.name == "nt" else "bin")
    / ("python.exe" if os.name == "nt" else "python")
)
PY = str(VENV_PY if VENV_PY.exists() else Path(sys.executable))

# --- knobs (kept obvious; not user-facing config) ---------------------------
ACCOUNTS_PORT = 4100
DAEMON_PORT = 8790
WEB_PORT = 5273
PROXY_PORT = 4000
STATE_DIR = V2 / ".agentd-hosted"  # deterministic hosted state (NOT ~/.agentd; keeps desktop separate)
DAEMON_TOKEN = "devtoken"
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "sk-agentd-local")

USE_PROXY = "--model-proxy" in sys.argv or "--proxy" in sys.argv
NO_WEB = "--no-web" in sys.argv

if load_dotenv:
    load_dotenv(V2 / ".env")  # provider keys (GEMINI_API_KEY, ...) into OUR env -> inherited by children

_procs: list[tuple[str, subprocess.Popen]] = []


# --- process helpers --------------------------------------------------------
def _pids_on_port(port: int) -> list[str]:
    if os.name != "nt":
        return []
    try:
        out = subprocess.check_output(["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        return []
    pids = set()
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(parts[-1])
    return list(pids)


def free_port(port: int) -> None:
    """Kill whatever is LISTENING on one of OUR ports (idempotent re-runs). Only our ports."""
    for pid in _pids_on_port(port):
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:  # pragma: no cover
        try:
            p = _proc_by_pid(pid)
            if p:
                p.terminate()
        except Exception:  # noqa: BLE001
            pass


def _proc_by_pid(pid):
    for _, p in _procs:
        if p.pid == pid:
            return p
    return None


def _pump(name: str, p: subprocess.Popen) -> None:
    for line in p.stdout:  # type: ignore[union-attr]
        sys.stdout.write(f"[{name}] {line.rstrip()}\n")
        sys.stdout.flush()


def spawn(name: str, cmd, env: dict | None = None, cwd: str | None = None, shell: bool = False):
    e = dict(os.environ)
    if env:
        e.update(env)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # we own teardown
    p = subprocess.Popen(
        cmd, env=e, cwd=cwd, shell=shell, creationflags=flags,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", bufsize=1,  # child logs have Unicode; don't cp1252-crash
    )
    _procs.append((name, p))
    threading.Thread(target=_pump, args=(name, p), daemon=True).start()
    return p


# --- readiness checks -------------------------------------------------------
def wait_http(url: str, timeout: int = 45) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    return False


def wait_port(port: int, timeout: int = 60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            # "localhost" resolves to BOTH ::1 and 127.0.0.1; create_connection tries each, so
            # this catches vite (binds IPv6 [::1]) AND the daemon (binds IPv4 127.0.0.1).
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except OSError:
            pass
        time.sleep(1)
    return False


# --- go ---------------------------------------------------------------------
def main() -> None:
    # our OWN stdout is cp1252 on Windows; child logs carry Unicode (emoji, box-drawing) — force
    # utf-8 with replacement so printing a child line never crashes the launcher.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    print(f"[dev] python={PY}")
    print(f"[dev] hosted state_dir={STATE_DIR}")
    ports = [ACCOUNTS_PORT, DAEMON_PORT] + ([PROXY_PORT] if USE_PROXY else []) + \
            ([] if NO_WEB else [WEB_PORT])
    for port in ports:
        free_port(port)

    # 1) accounts
    spawn("accounts", [PY, str(V2 / "deploy" / "accounts" / "run-local.py")],
          env={"AGENTD_ACCOUNTS_PORT": str(ACCOUNTS_PORT)})

    # 2) Model Proxy (opt-in)
    if USE_PROXY:
        spawn("model-proxy", [PY, str(V2 / "model_proxy" / "run-local.py")])

    # 3) daemon (accounts ON; direct provider calls unless --proxy)
    #    AGENTD_HOME gives it a SEPARATE rendezvous/logs from the desktop app's daemon (~/.agentd),
    #    so the exe and this hosted stack run side by side without the single-instance guard firing.
    #    AGENTD_STATE_DIR pins per-account data deterministically (AGENTD_HOME alone does NOT move it).
    daemon_env = {
        "PYTHONPATH": str(V2), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "AGENTD_HOST": "127.0.0.1", "AGENTD_PORT": str(DAEMON_PORT), "AGENTD_TOKEN": DAEMON_TOKEN,
        "AGENTD_HOME": str(STATE_DIR),
        "AGENTD_STATE_DIR": str(STATE_DIR),
        "AGENTD_WORKSPACE": str(STATE_DIR / "workspace"),  # contained base (per-account overrides it)
        "AGENTD_ACCOUNTS_URL": f"http://127.0.0.1:{ACCOUNTS_PORT}",
    }
    if USE_PROXY:
        daemon_env["AGENTD_MODEL_PROXY_URL"] = f"http://127.0.0.1:{PROXY_PORT}"
        daemon_env["AGENTD_MODEL_PROXY_KEY"] = MASTER_KEY
    spawn("daemon", [PY, "-m", "agent_runtime"], env=daemon_env, cwd=str(V2))

    # 4) web client (accounts mode)
    if not NO_WEB:
        spawn("web", "npm run dev:web", cwd=str(V2 / "clients" / "desktop"), shell=True,
              env={"VITE_AGENTD_ACCOUNTS_URL": f"http://127.0.0.1:{ACCOUNTS_PORT}",
                   "VITE_AGENTD_URL": f"ws://127.0.0.1:{DAEMON_PORT}"})

    # readiness board
    print("[dev] waiting for services...")
    ok_acc = wait_http(f"http://127.0.0.1:{ACCOUNTS_PORT}/health")
    ok_daemon = wait_port(DAEMON_PORT)
    ok_proxy = wait_port(PROXY_PORT) if USE_PROXY else None
    ok_web = wait_port(WEB_PORT) if not NO_WEB else None

    def tick(v):
        return "OK " if v else "-- "
    print("\n" + "=" * 56)
    print(f"  {tick(ok_acc)} accounts   http://127.0.0.1:{ACCOUNTS_PORT}")
    if USE_PROXY:
        print(f"  {tick(ok_proxy)} model-proxy http://127.0.0.1:{PROXY_PORT}")
    print(f"  {tick(ok_daemon)} daemon     ws://127.0.0.1:{DAEMON_PORT}  (accounts ON)")
    if not NO_WEB:
        print(f"  {tick(ok_web)} web        http://localhost:{WEB_PORT}   <-- OPEN THIS")
    print("=" * 56)
    print("[dev] Ctrl+C stops everything.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[dev] stopping...")
        for _, p in _procs:
            kill_tree(p.pid)


if __name__ == "__main__":
    main()
