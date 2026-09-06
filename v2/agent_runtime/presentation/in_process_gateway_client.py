"""InProcessGatewayClient — drive this daemon's own gateway as a client, without a socket.

WHY IT EXISTS. A plugin tool that runs OTHER agents (e2e_run) needs what a ws client has:
config.set, workspace.upload, chat.send, and the run's event stream. The socket way to get that
from inside the daemon is `one_shot_run`'s dial-back with `?act_as=` — and act_as is honoured
ONLY on a machine-token connection with accounts off. On a hosted daemon it does not authorise,
which is why cabbie's agent.toml has to deny `run_agent` outright. This client is the same
capability with no socket and no auth dance: it calls the gateway's own dispatch, carrying the
CURRENT context's account (the run that invoked the tool pinned it — gateway._run), so tenancy,
metering and workspace resolution are identical on desktop and hosted with zero forked code.

WHAT IT IS NOT. Not a general bypass: `call` admits only the small method set the e2e drive loop
speaks (ALLOWED below) — every one an ordinary client method that the real dispatch re-validates.
Widening the set is a deliberate one-line review, not a side effect.

It satisfies the e2e `live_driver` transport duck type (call / send / events) — the "third
transport" the harness was designed around; `WsGatewayTransport` is the socket sibling.
"""

from __future__ import annotations

import asyncio

from agent_runtime.infrastructure import accounts

#: The e2e drive loop's vocabulary, plus the cleanup call. Nothing else.
ALLOWED_METHODS = frozenset(
    {"chat.send", "chat.abort", "config.set", "workspace.upload", "sessions.delete"}
)


class InProcessGatewayClient:
    def __init__(self, gateway):
        self._gateway = gateway

    async def call(self, method: str, params: dict) -> dict:
        if method not in ALLOWED_METHODS:
            raise PermissionError(
                f"in-process gateway access does not allow '{method}' "
                f"(allowed: {', '.join(sorted(ALLOWED_METHODS))})"
            )
        # The FULL account dict of whoever's run is executing right now — pinned on the context
        # by gateway._run before the tool ran. None on desktop with accounts off, which is
        # exactly what a local machine-token connection carries too.
        account = accounts.current_account.get()
        return await self._gateway.dispatch_as(method, params, account)

    async def send(self, method: str, params: dict) -> None:
        # The socket transport fires chat.send without awaiting its ack; in-process the "ack" is
        # the handler returning, which is immediate (the run itself is a spawned task) — and a
        # refusal (unknown agent, busy session) surfaces HERE as an exception instead of dying
        # silently on a fire-and-forget socket write.
        await self.call(method, params)

    def events(self, session_key: str) -> "_TapEventStream":
        return _TapEventStream(self._gateway, session_key)


class _TapEventStream:
    """The event half of the transport contract: `next(timeout)` / `close()`. Registering in
    __init__ (not lazily on first read) is what guarantees no event can fall between chat.send
    and the first read."""

    def __init__(self, gateway, session_key: str):
        self._gateway = gateway
        self._key = session_key
        self._q = gateway.add_session_tap(session_key)

    async def next(self, timeout: float) -> dict | None:
        try:
            payload = await asyncio.wait_for(self._q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return (payload or {}).get("event") or {}

    def close(self) -> None:
        self._gateway.remove_session_tap(self._key, self._q)
