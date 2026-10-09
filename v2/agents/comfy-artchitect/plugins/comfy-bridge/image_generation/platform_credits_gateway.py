"""PlatformCreditsGateway — what the person's credits cover, and the charge for what was made.

Seedream images are paid with the PLATFORM's provider keys (Higgsfield, fal), so the person pays
in platform credits: the balance is read before a job is submitted, and the job's real cost is
debited once its images are in. Both go to the platform by NAME — `${AGENTD_ACCOUNTS_URL}` and
`${AGENTD_PLATFORM_TOKEN}` are filled in by the host as the request leaves (the internal key on a
hosted daemon, the signed-in person's own token on the desktop), so this code never holds either.

The debit is sent in DOLLARS: the platform converts at its own rate and markup (accounts /debit),
so no plugin has to know what a credit is worth.
"""

from __future__ import annotations

from agent_runtime.application.run_context import current_account_id, current_run_context
from agent_runtime.infrastructure.net.outbound import fetch

_ACCOUNTS = "${AGENTD_ACCOUNTS_URL}"
_AUTH = {"Authorization": "Bearer ${AGENTD_PLATFORM_TOKEN}"}


class PlatformCreditsGateway:
    def usd_left(self) -> float | None:
        """What the balance covers, in provider dollars — None when this account's credits are
        not enforced (a deployment's free tier). Raises when the platform cannot be read: a paid
        job is never submitted on a guess about whether it can be paid for."""
        account = current_account_id()
        url = f"{_ACCOUNTS}/credits/{account}" if account else f"{_ACCOUNTS}/me/credits"
        res = fetch(url, headers=_AUTH, timeout_s=15.0)
        if not res.ok:
            raise RuntimeError(f"could not read the credit balance: {res.error or f'HTTP {res.status}: {res.text[:200]}'}")
        body = res.json() or {}
        if body.get("credits_enforced") is False:
            return None
        return int(body.get("credits_remaining") or 0) / self._credits_per_usd()

    def debit(self, usd: float) -> None:
        """Charge `usd` of provider cost to the person. Raises when the platform refuses."""
        if usd <= 0:
            return
        ctx = current_run_context()
        payload = {"account_id": current_account_id(), "usd": round(usd, 6),
                   "agent_id": str(getattr(ctx, "agent_id", "") or "")}
        res = fetch(f"{_ACCOUNTS}/debit", method="POST", headers=_AUTH, json=payload, timeout_s=15.0)
        if not res.ok:
            raise RuntimeError(f"the platform did not record the charge: {res.error or f'HTTP {res.status}: {res.text[:200]}'}")

    @staticmethod
    def _credits_per_usd() -> float:
        res = fetch(f"{_ACCOUNTS}/pricing", timeout_s=10.0)
        rate = float((res.json() or {}).get("credits_per_usd") or 0.0) if res.ok else 0.0
        if rate <= 0:
            raise RuntimeError("could not read the platform's credit price (/pricing)")
        return rate


__all__ = ["PlatformCreditsGateway"]
