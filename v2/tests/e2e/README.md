# E2E smoke tests (booted daemon)

Empty on purpose — this tier exists and is wired (auto-`e2e` marker, excluded from the
Stage-1 gates) but has no tests yet.

The failure class this tier is for is the one in-process tests structurally cannot catch:
lost RPC replies over the wire, a daemon that boots dead, stale config being read from the
wrong place, client/server protocol drift. Every one of those has cost real debugging time.

First planned test (`test_daemon_smoke.py`):

1. Boot the daemon as a subprocess with `AGENTD_HOME` pointed at a tmp dir and a random port.
2. Wait for the rendezvous/gateway file, connect a real WebSocket client with the token.
3. Call `capabilities.list` and one trivial RPC round-trip; assert well-formed replies
   (no LLM call — model-touching paths use a stubbed provider or are skipped).
4. Shut down cleanly; assert the process exits.

Keep these few and fast (seconds, not minutes): they prove the wiring, not the features —
features are proven in `tests/integration`.
