"""create_webhook — mint a webhook URL that an external service can POST to, to RUN an agent (D).

The agent sets up an inbound trigger by chatting: it picks the agent to run (and an optional
default task), and gets back a ``/hook/<id>`` URL + secret to paste into GitHub / CI / any
service. When that service POSTs ``{"task": "..."}`` (optionally ``{"agentId": "..."}``) with the
secret, the gateway runs the agent — no human in the loop. The mint/persist logic is injected as
a callable so this tool stays out of the presentation layer."""

from __future__ import annotations

from . import Tool, ToolResult


class CreateWebhookTool(Tool):
    name = "create_webhook"
    label = "Create Webhook"
    concurrency = "sequential"  # mutates the hook registry / config
    default_retryable = False
    description = (
        "Create a webhook URL an external service (GitHub, CI, Stripe, any system) can POST to in "
        "order to TRIGGER an agent to run — for 'when X happens, do Y' automation. Give the "
        "`agent` to run and optionally a default `task` (the POST body can override the task, and "
        "the agentId if you allow it). Returns a URL + secret to configure in that service. The "
        "secret is like a password — anyone with the URL+secret can trigger runs, so share it "
        "only with the intended service."
    )
    parameters = {
        "type": "object",
        "required": ["agent"],
        "properties": {
            "agent": {"type": "string", "description": "the agent id to run when the hook fires"},
            "id": {"type": "string", "description": "optional short id for the hook (else random)"},
            "task": {
                "type": "string",
                "description": "optional default task to run (the POST body can override it)",
            },
        },
    }

    def __init__(self, create_fn):
        self._create = create_fn  # async (params: dict) -> dict  (the gateway's _create_webhook)

    async def execute(self, tool_call_id, params, abort, on_update=None):
        agent = (params.get("agent") or "").strip()
        if not agent:
            return ToolResult.text("create_webhook needs an 'agent' to run", is_error=True)
        try:
            r = await self._create(
                {"agent": agent, "id": params.get("id"), "task": params.get("task")}
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"create_webhook failed: {type(e).__name__}: {e}", is_error=True)
        if not r.get("created"):
            return ToolResult.text(f"could not create the webhook: {r.get('error')}", is_error=True)
        return ToolResult.text(
            f"Webhook created for agent '{r['agent']}'.\n"
            f"URL:    {r['url']}\n"
            f"Secret: {r['secret']}\n"
            f'Configure the external service to POST JSON {{"task": "…"}} to that URL with '
            f"header `X-Webhook-Secret: <secret>` (or `Authorization: Bearer <secret>`). "
            f"{'Saved.' if r.get('persisted') else 'NOT persisted — set AGENTD_CONFIG to survive restart.'}",
            details=r,
        )
