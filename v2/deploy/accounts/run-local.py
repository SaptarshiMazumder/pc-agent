"""Run the Platform Accounts service LOCALLY (SQLite) on :4100.

    python deploy/accounts/run-local.py          # from v2/, or anywhere

Then point agentd at it (default OFF unless this URL is set):
    AGENTD_ACCOUNTS_URL=http://127.0.0.1:4100

The DB lives at deploy/accounts/data/accounts.db (override with AGENTD_ACCOUNTS_DB).
"""

import os
from pathlib import Path

import uvicorn

_here = Path(__file__).resolve()
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("AGENTD_ACCOUNTS_DB", str(_here.parent / "data" / "accounts.db"))

# import by module path so `app` is importable no matter the CWD
import sys

sys.path.insert(0, str(_here.parent))
from app import app  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("AGENTD_ACCOUNTS_PORT", "4100"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
