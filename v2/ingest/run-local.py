"""Run the Ingest service LOCALLY on :4200.

    python ingest/run-local.py                 # from v2/, or anywhere

This is what makes the desktop diagnostics toggle (plan 5.1) testable without deploying:
`flavors/hosted-dev` points `ingest_url` at 127.0.0.1:4200, so `AGENTD_FLAVOR=hosted-dev npm run
dev` + the toggle in Settings sends real batches here, and every accepted event prints as an EMF
line on THIS process's stdout — which is exactly what CloudWatch would have extracted.

`deploy/dev.py` starts it alongside accounts and the daemon; run it standalone only when you want
its output in a terminal of its own.
"""

import os
import sys
from pathlib import Path

import uvicorn

_here = Path(__file__).resolve()
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("AGENTD_SERVICE", "ingest")
# Off locally: a rate limit exists to stop one machine in a reboot loop spending our CloudWatch
# budget, and there is no budget here — while a limit that fires mid-test looks like a bug.
os.environ.setdefault("INGEST_RATE_LIMIT", "0/0")

# import by module path so `app` is importable no matter the CWD
sys.path.insert(0, str(_here.parent))
from app import app  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("AGENTD_INGEST_PORT", "4200"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
