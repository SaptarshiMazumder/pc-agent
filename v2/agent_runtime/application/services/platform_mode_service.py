"""PlatformModeService — the use case: "which keys pay for this machine's model calls".

Three ports, no I/O of its own: the stored preference, the signed-in identity, and the thing that
actually switches billing on and off.

WHY A SERVICE AND NOT A BOOLEAN. The mode has to be re-asserted at moments that have nothing to do
with a user pressing a button — at boot, and immediately after a sign-in — because "default cloud"
is only meaningful if something applies it when the preconditions first become true. Signing in is
exactly that moment: before it there was no token to pay with, after it there is.

    apply()   make reality match the rule           (boot, post-login)
    choose()  record what the user pressed, then apply

THE DEFAULT AND THE OVERRIDE ARE BOTH REAL. `apply()` will put an install into Cloud on its own,
but only while the user has expressed no preference. The moment they press Local that is written
down, and every later boot and every later sign-in leaves them there. A default that could not be
refused would not be a default.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.model_proxy_binder import ModelProxyBinder
from agent_runtime.application.interfaces.platform_mode_store import PlatformModeStore
from agent_runtime.application.interfaces.session_token_store import SessionTokenStore
from agent_runtime.domain import platform_mode


class PlatformModeService:
    def __init__(
        self,
        modes: PlatformModeStore,
        tokens: SessionTokenStore,
        proxy: ModelProxyBinder,
    ):
        self._modes = modes
        self._tokens = tokens
        self._proxy = proxy

    # ------------------------------------------------------------------ reading
    def preference(self) -> str:
        """What the user chose, or UNSET. Reported separately from `mode()` so a UI can show
        "Cloud (default)" differently from "Cloud (your choice)" — and so pressing Local on an
        install that is already local still records something."""
        return platform_mode.normalize(self._modes.read())

    def mode(self) -> str:
        """The mode this daemon is actually in."""
        return platform_mode.resolve(
            self.preference(), self._proxy.available, self._tokens.read().signed_in
        )

    def session_token(self) -> str:
        """The stored identity token. Exposed so the transport layer can bind the proxy without
        the CALLER supplying a credential — an agent's settings screen has none, by design."""
        return self._tokens.read().token

    @property
    def can_use_cloud(self) -> bool:
        """Is Cloud reachable at all? A UI disables the control on this, and says why: without a
        proxy URL there is nothing to switch to, and without a sign-in there is nobody to bill."""
        return self._proxy.available and self._tokens.read().signed_in

    # ------------------------------------------------------------------ acting
    def apply(self) -> str:
        """Make billing match the rule. Idempotent — safe on every boot and after every login."""
        target = self.mode()
        if target == platform_mode.CLOUD:
            if not self._proxy.bound:
                self._proxy.bind(self._tokens.read().token)
        elif self._proxy.bound:
            self._proxy.unbind()
        return target

    def remember(self, mode: str) -> None:
        """Record a choice whose EFFECT has already been applied by the caller.

        Split from `choose` for the two legacy entry points, `platform.connect` and
        `platform.disconnect`: they bind and unbind themselves (and emit telemetry around it), so
        re-applying here would be a second, redundant reconfigure. What they were missing is the
        memory of the choice, which is this."""
        self._modes.write(platform_mode.normalize(mode))

    def choose(self, mode: str) -> str:
        """Record an explicit choice and act on it.

        A request for Cloud that cannot be honoured RAISES rather than silently landing in Local.
        The user pressed a button; if it did nothing they need to be told which half is missing,
        not left looking at a toggle that sprang back."""
        wanted = platform_mode.normalize(mode)
        if wanted == platform_mode.UNSET:
            raise ValueError(f"unknown mode {mode!r} — expected 'local' or 'cloud'")
        if wanted == platform_mode.CLOUD and not self._proxy.available:
            raise ValueError("this build has no model proxy configured, so Cloud mode is unavailable")
        if wanted == platform_mode.CLOUD and not self._tokens.read().signed_in:
            raise ValueError("sign in first — Cloud mode meters model calls to your account")
        self._modes.write(wanted)
        return self.apply()
