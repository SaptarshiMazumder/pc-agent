"""simple_login — log into a site with vaulted credentials, handling username/email + password
and (live) 2FA OTP, WITHOUT the password ever reaching the model.

Flow: the agent calls ``simple_login(site="hotpepper")`` on the login page. The tool reads the
saved credential from the encrypted vault, fills the form via the browser provider (the password
is typed INTERNALLY — never in the tool args, the result, the event log, or the model's context),
and submits. If the site then asks for a one-time code, the tool returns ``OTP_REQUIRED``; the
agent asks the user for the code (ephemeral, so chat is fine) and calls
``simple_login(site=..., otp="123456")`` to finish. No saved credential -> it says so and NEVER
asks for a password in chat.
"""

from __future__ import annotations

import logging

from agentd.application.run_context import current_run_context

from . import Tool, ToolResult

log = logging.getLogger("agentd")

# generic fallbacks when a site has no configured selector (per-site selectors are more reliable)
_USER_HEURISTIC = "input[type=email], input[type=text]:not([type=password]):not([type=hidden])"
_PASS_HEURISTIC = "input[type=password]"
_OTP_HEURISTIC = "input[autocomplete=one-time-code], input[name*=otp i], input[name*=code i], input[type=tel]"


class SimpleLoginTool(Tool):
    name = "simple_login"
    label = "Login"
    concurrency = "sequential"        # drives the one shared browser; never run two at once
    default_retryable = False         # side-effecting (submits a login); never auto-retry
    description = (
        "Log into a website using YOUR saved credentials for it (connected once, securely — you "
        "never see or handle the password). Open the site's login page, then call "
        "simple_login(site=\"<name>\"); it fills username/email + password and submits. If the "
        "site asks for a one-time 2FA code, it returns OTP_REQUIRED — ask the user for the code, "
        "then call again with otp=\"<code>\". If there's no saved login, it returns a one-time "
        "setup link — share it ONLY if your instructions permit giving setup links to the person "
        "you're talking to; NEVER ask for a password in chat.")
    parameters = {
        "type": "object", "required": ["site"],
        "properties": {
            "site": {"type": "string", "description": "the saved-login name, e.g. 'hotpepper'"},
            "otp": {"type": "string",
                    "description": "the one-time 2FA code — ONLY on the resume call after OTP_REQUIRED"},
        },
    }

    def __init__(self, store, browser_manager, connect_tokens=None, public_url=""):
        self._store = store
        self._mgr = browser_manager
        self._connect = connect_tokens          # mints the one-time /connect link (or None)
        self._public_url = (public_url or "").strip()

    async def execute(self, tool_call_id, params, abort, on_update=None):
        site = (params.get("site") or "").strip()
        otp = (params.get("otp") or "").strip()
        if not site:
            return ToolResult.text("simple_login needs a 'site'", is_error=True)
        ctx = current_run_context()
        agent_id = (ctx.agent_id if ctx else "") or "main"
        cred = self._store.get(agent_id, site)
        if cred is None:
            return ToolResult.text(self._no_creds_message(agent_id, site), is_error=True)
        try:
            await self._mgr.ensure()
            page = self._mgr.resolve_target(None)
            if not otp:                                   # fresh login: fill the form
                await page.goto(cred.login_url, wait_until="domcontentloaded")
                await page.fill(cred.user_selector or _USER_HEURISTIC, cred.username)
                pass_sel = cred.pass_selector or _PASS_HEURISTIC
                await page.fill(pass_sel, cred.password)  # SECRET — typed internally, never returned
                await self._submit(page, cred, pass_sel)
                await self._settle()
                if await self._present(page, cred.otp_selector or _OTP_HEURISTIC):
                    return ToolResult.text(
                        f"OTP_REQUIRED: '{site}' is asking for a one-time 2FA code. Ask the user "
                        f"for it, then call simple_login(site='{site}', otp='<code>').")
            else:                                         # resume: type the OTP (ephemeral, ok)
                otp_sel = cred.otp_selector or _OTP_HEURISTIC
                await page.fill(otp_sel, otp)
                await self._submit(page, cred, otp_sel)
                await self._settle()
            if await self._logged_in(page, cred):
                return ToolResult.text(f"Logged in to {site}.")
            return ToolResult.text(
                f"Login to {site} didn't complete — the saved credentials or the code may be wrong.",
                is_error=True)
        except Exception as e:  # noqa: BLE001 — only the exception TYPE, never a message (no secret leak)
            return ToolResult.text(f"login error on {site}: {type(e).__name__}", is_error=True)

    def _no_creds_message(self, agent_id: str, site: str) -> str:
        # PROVIDE the one-time setup link, but do NOT command sharing it. WHETHER to share it is
        # decided by the AGENT'S OWN instructions — no owner/customer/admin concept is baked in
        # here. (An assistant told to help its owner may share it; an agent told not to share
        # setup links with the people it talks to must not.)
        if self._connect is not None and self._public_url:
            from agentd.infrastructure.credentials import ConnectTokenStore
            token = self._connect.mint(agent_id, site)
            link = ConnectTokenStore.link(self._public_url, token)
            log.info("simple_login: no credential for agent=%s site=%s", agent_id, site)
            return (f"No saved login for '{site}'. A one-time setup link (expires ~15 min):\n{link}\n"
                    f"Share this link ONLY if your instructions permit giving setup links to the "
                    f"person you're talking to; otherwise don't share it. Never ask for a password "
                    f"in chat.")
        return (f"No saved login for '{site}' (and no setup link is configured — set "
                f"AGENTD_PUBLIC_URL). Never ask for a password in chat; respond per your instructions.")

    async def _submit(self, page, cred, field_sel) -> None:
        if cred.submit_selector:
            await page.click(cred.submit_selector)
        else:
            await page.press(field_sel, "Enter")

    async def _settle(self) -> None:
        try:
            await self._mgr.settle()
        except Exception:  # noqa: BLE001
            pass

    async def _present(self, page, selector) -> bool:
        if not selector:
            return False
        try:
            return await page.query_selector(selector) is not None
        except Exception:  # noqa: BLE001
            return False

    async def _logged_in(self, page, cred) -> bool:
        if cred.success_selector:
            return await self._present(page, cred.success_selector)
        # heuristic: the password field is gone -> we left the login form
        return not await self._present(page, cred.pass_selector or _PASS_HEURISTIC)
