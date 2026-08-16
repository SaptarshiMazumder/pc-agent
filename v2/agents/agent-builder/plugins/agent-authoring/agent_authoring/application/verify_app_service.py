"""VerifyAppService — open the window that was just built and report what is actually on screen.

THE GAP THIS FILLS. `validate_agent` proves an agent is well-FORMED. `agentd ask` proves its
brain RUNS. Neither opens the window, and a window is the one part that can be perfectly built,
perfectly served, and blank — a wrong asset path, a crash on mount, an event name that never
fires. Every one of those looks identical to success from the author's side: the build printed
no errors and the file is on disk.

TWO HALVES, deliberately.

  The GENERIC half runs the same way for every agent and needs to know nothing about it: did the
  assets load, did anything render, did it crash, did the socket open, does the layout overflow.

  The DRIVING half is the caller's: click this, type there. No fixed checklist can know whether a
  dashboard's Refresh fetched numbers or a queue advanced — but the model that just built it
  knows exactly which control is supposed to do what. So the service drives whatever steps it is
  given and re-reports, rather than pretending to a knowledge it does not have.

THE DRIVER IS INJECTED. Everything that decides good-or-bad lives in domain/app_checks.py over a
plain observation, so the rules are tested with a dict and no browser at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agent_authoring.domain.app_checks import Finding, PageObservation, check_page
from agent_authoring.domain.finding import ERROR, LEVEL_RANK


class VerifyError(Exception):
    """The verification could not START. Carries the message the caller should show verbatim —
    these are setup problems (no window declared, no daemon, no browser), never verdicts."""


@dataclass
class Step:
    """One interaction. ``target`` is matched on visible text first, then as a CSS selector —
    the model knows what a control SAYS far more reliably than what its selector is."""

    action: str  # click | type | press | wait
    target: str = ""
    text: str = ""


class PageDriver(Protocol):
    """The browser, reduced to what verification needs. Implemented by Playwright in
    infrastructure/; faked in tests."""

    def open(self, url: str) -> PageObservation: ...

    def drive(self, steps: list[Step]) -> PageObservation: ...

    def sign_in(self, email: str, password: str) -> PageObservation: ...

    def close(self) -> None: ...


@dataclass
class VerifyResult:
    agent_id: str
    url: str
    findings: list[Finding] = field(default_factory=list)
    observation: PageObservation | None = None
    after_steps: PageObservation | None = None
    steps_run: list[str] = field(default_factory=list)
    signed_in: bool = False

    @property
    def blocked(self) -> bool:
        """The sign-in gate stopped it. NOT a failure and NOT a pass — a THIRD outcome, because
        both of the other two are lies here: the agent is not broken, and it is not checked."""
        obs = self.after_steps or self.observation
        return bool(obs and obs.sign_in_gate)

    @property
    def passed(self) -> bool:
        return not self.blocked and not any(f.is_error for f in self.findings)

    @property
    def screenshots(self) -> list[str]:
        shots = [o.screenshot for o in (self.observation, self.after_steps) if o and o.screenshot]
        return shots


class VerifyAppService:
    def __init__(self, reader, driver_factory, gateway_reader, screenshot_dir):
        """
        :param reader: agent id -> directory.
        :param driver_factory: (screenshot: bool) -> PageDriver. A factory, not an instance: a
            browser is expensive and must not be held open between calls.
        :param gateway_reader: () -> the daemon's rendezvous info (host/port/token), or None.
            Injected so the TOKEN is resolved here and never travels through the model.
        :param screenshot_dir: where screenshots land.
        """
        self._reader = reader
        self._driver_factory = driver_factory
        self._gateway = gateway_reader
        self._shots = Path(screenshot_dir)

    def verify(
        self,
        agent_id: str,
        steps: list[Step] | None = None,
        email: str = "",
        password: str = "",
        screenshot: bool = False,
    ) -> VerifyResult:
        agent_dir = self._reader.agent_dir(agent_id)
        if agent_dir is None:
            known = ", ".join(self._reader.known_ids()) or "(none)"
            raise VerifyError(f"no agent '{agent_id}'. Known agents: {known}")

        entry = self._entry(agent_dir, agent_id)
        stale = _stale_build(agent_dir)

        info = self._gateway()
        if info is None:
            raise VerifyError(
                "no daemon is running, so there is nothing serving this window. Start agentd "
                "and try again."
            )

        # `verify=1` asks the SDK's gate to stand aside — and it only obeys where the daemon says
        # sign-in is not REQUIRED, which is every daemon this agent can run on (it is local-only,
        # and locally an accounts URL advertises sign-in rather than demanding it). So the flag
        # grants nothing the daemon would have refused; it removes a prompt that was going to
        # stop a browser which needed no account in the first place.
        url = (
            f"http://{info.host}:{info.port}/apps/{agent_id}/"
            f"?token={info.token}&scope=agent:{agent_id}&verify=1"
        )
        # The factory takes the screenshot decision, so the driver never captures an image
        # nobody asked for — the cost is in TAKING it, not in whether it is later attached.
        driver = self._driver_factory(screenshot)
        signed_in = False
        try:
            observation = driver.open(url)
            # Only when it is actually blocked. Signing in unasked would burn a real login on
            # every run and hide the fact that the gate was never in the way.
            if observation.sign_in_gate and email and password:
                observation = driver.sign_in(email, password)
                signed_in = not observation.sign_in_gate
            after = driver.drive(steps) if steps else None
        finally:
            driver.close()

        findings = list(stale)
        findings += check_page(observation)
        if after is not None:
            # Re-checked AFTER the interaction, because most windows are fine until you touch
            # them: the handler that throws only throws on click.
            findings += [_after(f) for f in check_page(after)]
        findings.sort(key=lambda f: LEVEL_RANK.get(f.level, 9))

        return VerifyResult(
            agent_id=agent_id,
            url=f"/apps/{agent_id}/",  # neither the token nor the credentials are echoed back
            findings=findings,
            observation=observation,
            after_steps=after,
            steps_run=[f"{s.action} {s.target}".strip() for s in (steps or [])],
            signed_in=signed_in,
        )

    def _entry(self, agent_dir: Path, agent_id: str) -> Path:
        """The declared window, or a refusal that says which of the two things is missing."""
        import tomllib

        toml_path = agent_dir / "agent.toml"
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise VerifyError(f"could not read {toml_path}: {e}") from e

        app = data.get("app")
        if not isinstance(app, dict):
            raise VerifyError(
                f"'{agent_id}' declares no [app], so it has no window to verify. That is a "
                f"legitimate shape — a chat-only agent is checked with `agentd ask` instead."
            )
        entry = agent_dir / (app.get("entry") or "ui/index.html")
        if not entry.is_file():
            raise VerifyError(
                f"[app] entry '{app.get('entry') or 'ui/index.html'}' does not exist, so the "
                f"window would 404. If this app builds from app/, run `npm run build` first."
            )
        return entry


def _after(finding: Finding) -> Finding:
    """Mark a finding as coming from the interaction rather than the load — otherwise a click
    that breaks the page reads as if the page was broken all along."""
    from dataclasses import replace

    return replace(finding, message=f"after your steps: {finding.message}")


def _stale_build(agent_dir: Path) -> list[Finding]:
    """A built app whose source is newer than its output.

    THE MOST WASTEFUL FAILURE AVAILABLE: everything passes, against the previous screen. The
    author reads a green result about code they have already changed, and the change they are
    verifying is not in the thing being verified.
    """
    src = agent_dir / "app" / "src"
    if not (agent_dir / "app" / "package.json").is_file() or not src.is_dir():
        return []  # not a built app: ui/ IS the source
    built = [p for p in (agent_dir / "ui").rglob("*") if p.is_file()]
    if not built:
        return [
            Finding(
                level=ERROR,
                code="UI_NOT_BUILT",
                message="app/ has sources but ui/ is empty — nothing has been built",
                path="app/",
                fix="cd app && npm install && npm run build",
            )
        ]
    newest_src = max((p.stat().st_mtime for p in src.rglob("*") if p.is_file()), default=0.0)
    if newest_src <= max(p.stat().st_mtime for p in built):
        return []
    return [
        Finding(
            level=ERROR,
            code="UI_BUILD_STALE",
            message="app/src is newer than ui/ — the window being served is NOT the code you "
            "last wrote, so anything checked here is about the previous build",
            path="app/",
            fix="cd app && npm run build, then verify again",
        )
    ]
