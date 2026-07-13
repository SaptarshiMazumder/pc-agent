"""Built-in 'browser' bundle — the browser dispatcher tool + simple_login.

The browser PROVIDER (the shared Playwright/CDP session) stays in agentd core as an injected
dependency; this bundle receives it via DI (``ctx.browser``) and registers the TOOL the model
calls. Gating ported from build_tools: no browser provider => nothing registers; simple_login
needs BOTH the browser AND a credential vault (``ctx.credential_store``; AGENTD_VAULT_KEY) — absent
either, it's gone (the password never reaches the model).
"""

from __future__ import annotations


def register(api, ctx):
    if ctx.browser is None:
        return
    from browser_tool import BrowserTool

    api.register_tool(BrowserTool(ctx.config, ctx.browser))
    if ctx.credential_store is not None:
        from login_tool import SimpleLoginTool

        api.register_tool(
            SimpleLoginTool(
                ctx.credential_store,
                ctx.browser,
                connect_tokens=ctx.connect_token_store,
                public_url=getattr(ctx.config, "public_url", ""),
            )
        )
