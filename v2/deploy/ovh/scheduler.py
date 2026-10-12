"""One external UTC billing clock. Persist successful slots, retry failures.

The accounts endpoints own transaction/idempotency rules. A failed request must
not be marked completed; retrying after an ambiguous response relies on those
same rules used by the previous AWS scheduler.
"""

import json
import os
import sqlite3
import time
import urllib.request
from datetime import UTC, datetime


def due(now):
    day = now.strftime("%Y-%m-%d")
    result = [("/subscriptions/renew-due", now.strftime("%Y-%m-%dT%H"))]
    for minute, path in ((5, "/ledger/close-expired"), (20, "/ledger/snapshot")):
        if now.hour * 60 + now.minute >= minute:
            result.append((path, day))
    return result


def tick(db, now):
    for path, slot in due(now):
        if db.execute("SELECT 1 FROM completed WHERE path=? AND slot=?", (path, slot)).fetchone():
            continue
        try:
            request = urllib.request.Request(
                os.environ["ACCOUNTS_URL"] + path,
                data=b"{}",
                method="POST",
                headers={
                    "X-Internal-Key": os.environ["ACCOUNTS_INTERNAL_KEY"],
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=300) as response:
                result = json.load(response)
                if result.get("ok") is False:
                    raise ValueError("accounts job refused")
            with db:
                db.execute("INSERT OR IGNORE INTO completed VALUES (?,?)", (path, slot))
                db.execute("DELETE FROM completed WHERE slot<?", (now.strftime("%Y-%m") + "-01",))
            print(json.dumps({"job": path, "slot": slot, "ok": True}), flush=True)
        except Exception:
            print(json.dumps({"job": path, "slot": slot, "ok": False}), flush=True)


if __name__ == "__main__":
    db = sqlite3.connect("/state/scheduler.db")
    db.execute(
        "CREATE TABLE IF NOT EXISTS completed (path TEXT, slot TEXT, PRIMARY KEY(path,slot))"
    )
    while True:
        tick(db, datetime.now(UTC))
        time.sleep(60)
