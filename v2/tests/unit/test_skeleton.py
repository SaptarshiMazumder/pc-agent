"""The skeleton: a complete, working agent window, copied into every agent that gets one.

TWO FAILURES ARE PINNED HERE, and both are silent for the author and fatal for everyone else.

The first is packaging: a React app reaches the SDK through `@agentd/client`, and in this repo the
workspace resolves that with a relative `file:` path into `clients/sdk-js`. An agent scaffolded
into a user's own agents directory has nothing at that path — `npm install` fails and the app can
never be built by anyone who did not write it. The skeleton therefore carries the SDK.

The second used to be pedagogy: the skill had to send the model to `agents/samples/` to learn the
shape of a window, and reading them was optional. That is what the skeleton replaced. The tests
that policed how the samples were referenced are gone with them; what is checked now is that the
artifact itself is complete, because it is copied whether or not anybody reads it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_authoring.application.scaffold_react_app_service import (
    REQUIRED,
    ReactScaffoldError,
    ScaffoldReactAppService,
)
from agent_authoring.bundle_layout import BundleLayout
from agent_authoring.domain.js_comment_stripper import JsCommentStripper

ROOT = Path(__file__).resolve().parents[2]
SKELETON = BundleLayout.SKELETON_ROOT
SKILL = ROOT / "agents" / "agent-builder" / "skills" / "build-agent" / "SKILL.md"


# --------------------------------------------------------------------------- the skeleton


@pytest.mark.parametrize("rel", REQUIRED)
def test_every_file_the_starter_promises_exists(rel):
    """A missing piece is a build error naming nothing useful, several steps later."""
    assert (SKELETON / rel).is_file(), (
        f"{rel} is missing from {SKELETON}. For vendor/, run `npm run build` in clients/sdk-js."
    )


def test_the_starter_does_not_depend_on_the_sdk_package():
    """THE WHOLE POINT. A dependency on '@agentd/client' resolves only inside this repo, so an
    agent that declares one cannot be built by whoever receives it."""
    pkg = json.loads((SKELETON / "package.json").read_text(encoding="utf-8"))
    declared = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    assert "@agentd/client" not in declared, (
        "the skeleton declares @agentd/client — that path exists only in this repo; "
        "the SDK is vendored into vendor/ and aliased instead"
    )


def test_the_sdk_is_aliased_for_both_the_bundler_and_the_compiler():
    """Two resolvers, two configs. Vite alone leaves TypeScript reporting 'cannot find module'
    on every file that imports the SDK; tsconfig alone builds nothing."""
    assert "@agentd/client" in (SKELETON / "vite.config.ts").read_text(encoding="utf-8")
    tsconfig = (SKELETON / "tsconfig.json").read_text(encoding="utf-8")
    assert '"@agentd/client"' in tsconfig


def test_the_vendored_sdk_is_the_real_build():
    """A hand-maintained copy drifts from the daemon it talks to. This one is written by the
    SDK's own build (clients/sdk-js/scripts/vendor.mjs), so it cannot."""
    vendored = (SKELETON / "vendor" / "agentd-client.js").read_bytes()
    built = (ROOT / "clients" / "sdk-js" / "dist" / "index.js").read_bytes()
    assert vendored == built, "vendor/agentd-client.js is stale — run `npm run build` in clients/sdk-js"


def test_the_build_writes_where_the_daemon_reads():
    """app/ is source and ui/ is what ships. Point outDir anywhere else and the window 404s."""
    config = (SKELETON / "vite.config.ts").read_text(encoding="utf-8")
    assert "'../ui'" in config
    # Served under /apps/<id>/, so absolute asset URLs request the daemon root and every chunk
    # 404s — a blank window with a clean console.
    assert "base: './'" in config


COMMON = BundleLayout.COMMON_ROOT


def _same(a, b) -> bool:
    """Compared as normalised text: git checks these out CRLF on Windows and LF elsewhere, so a
    byte comparison would fail on the platform rather than on the content."""
    def norm(p):
        return p.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")

    return norm(a) == norm(b)


def test_the_skeleton_ships_a_working_window_not_a_stub():
    """THE BET, REVERSED. This used to assert the opposite — that the starter shipped no
    components, no layout and no hooks, because what a window should BE was a judgement about the
    agent and the material for that judgement was `agents/samples/`.

    Reading a sample is optional, and the parts that got skipped were the ones that are invisible
    when missing: signed in but never shows a balance, or shows a balance with no way to join the
    organization that paid for it. So the skeleton ships the whole thing and the model edits it."""
    src = SKELETON / "src"
    assert (src / "App.tsx").is_file(), "no shell"
    assert (src / "state" / "store.ts").is_file(), "nothing holds the conversation"
    assert (src / "styles.css").is_file(), "no stylesheet"
    assert (src / "agentd" / "run-events.ts").is_file(), "nothing folds run events into the thread"
    assert (src / "components" / "Composer.tsx").is_file(), "nothing to type into"
    assert (src / "components" / "Thread.tsx").is_file(), "nothing renders the conversation"


def test_the_skeleton_renders_all_four_shared_screens():
    """Each is mandatory and each fails silently when missing — `validate_agent` refuses to
    package an agent without them. Shipping the files is not enough; something has to render
    them, which is exactly the hole the component rules exist to close."""
    app = (SKELETON / "src" / "App.tsx").read_text(encoding="utf-8")
    for module in ("common/credits/Credits", "common/orgs/OrgView", "common/settings/Settings"):
        assert module in app, f"App.tsx never renders {module}"
    main = (SKELETON / "src" / "main.tsx").read_text(encoding="utf-8")
    assert "common/auth/Gate" in main, "main.tsx never gates on sign-in"


def test_the_skeletons_copy_of_the_shared_modules_matches_the_source():
    """The skeleton carries `src/common/` so it can be built and typechecked on its own, but the
    validator compares what an AGENT ships against `templates/_common/`. If the two drift, a
    freshly scaffolded agent fails `UI_COMMON_MODIFIED` the moment it is validated — which reads
    as the author having edited something they never touched.

    (The scaffolder writes `_common/` over the skeleton's copy for exactly this reason. This test
    is the other half: it keeps the copy honest for anyone building the skeleton directly.)"""
    for src in sorted(f for f in COMMON.rglob("*") if f.is_file()):
        mirror = SKELETON / "src" / "common" / src.relative_to(COMMON)
        assert mirror.is_file(), f"the skeleton is missing {src.relative_to(COMMON)}"
        assert _same(mirror, src), f"the skeleton's {src.relative_to(COMMON)} has drifted"


def test_the_shipped_entry_gates_the_whole_app():
    """A starter that shipped a main.tsx WITHOUT this would be worse than shipping none — it looks
    like the question was already handled.

    IT WRAPS RATHER THAN BLOCKS. This used to assert that `signInFirst()` was awaited BEFORE
    `root.render` — the gate was a vanilla-DOM panel that painted itself over the page, so the
    entry point had to be an async IIFE that rendered nothing until it resolved. It is the
    assistant's React card now, so the requirement moved: the app renders immediately and `<Gate>`
    swaps in the card only if the daemon says an account is required. A blank window while a status
    probe runs is indistinguishable from a broken one.
    """
    main = (SKELETON / "src" / "main.tsx").read_text(encoding="utf-8")
    # COMMENTS ARE NOT CODE. This file explains at the top what it replaced and names the calls it
    # replaced, so the "must not come back" assertions below read the STRIPPED text — the same
    # stripper the validator uses, and for the same reason: a check that cannot tell an explanation
    # from the mistake it describes fires on the most careful file it will ever see.
    code = JsCommentStripper().strip(main)

    # THROUGH THE COMMON MODULE, not by reaching for the SDK here. One place per agent knows how
    # signing in works, and it is the copied module every other agent has too.
    assert "./common/auth/Gate" in main
    assert "<Gate>" in main

    # It has to be OUTSIDE the app, not a branch somewhere inside it: on a hosted daemon the
    # session token IS the socket credential, so an app that gates after connecting never gets to.
    assert main.index("<Gate>") < main.index("<App />"), "the gate wraps the app"

    # And the deleted vanilla gate must not come back. Both spellings: the SDK call and the
    # common module's old wrapper around it.
    assert "mountSignInGate" not in code
    assert "signInFirst" not in code


# --------------------------------------------------------------------------- scaffolding


class FakeReader:
    def __init__(self, root: Path):
        self._root = root

    def agent_dir(self, agent_id: str):
        d = self._root / agent_id
        return d if d.is_dir() else None

    def known_ids(self):
        return ["known"]


@pytest.fixture
def service(tmp_path):
    (tmp_path / "known").mkdir()
    return ScaffoldReactAppService(FakeReader(tmp_path), SKELETON)


def test_it_writes_a_buildable_project(service, tmp_path):
    result = service.scaffold("known")

    for rel in REQUIRED:
        assert rel in result.written
        assert (tmp_path / "known" / "app" / rel).is_file()


def test_it_copies_every_common_module(service, tmp_path):
    """Accounts and money arrive with the scaffold, verbatim. Copied rather than imported because
    an agent is a shipped artifact and no workspace path survives being published."""
    result = service.scaffold("known")

    copied = [r for r in result.written if r.startswith("src/common/")]
    assert copied, "no shared modules landed"
    for rel in copied:
        dest = tmp_path / "known" / "app" / rel
        assert dest.is_file()
        src = COMMON / rel[len("src/common/") :]
        assert _same(dest, src), f"{rel} is not a verbatim copy"


def test_the_common_modules_cover_accounts_and_money(service, tmp_path):
    """The set itself, asserted — an agent scaffolded without one of these cannot be published,
    and the failure would surface as a validator error rather than here."""
    result = service.scaffold("known")
    copied = {r[len("src/common/") :] for r in result.written if r.startswith("src/common/")}

    assert {"auth/SignIn.tsx", "auth/useAuth.ts", "auth/ProfileMenu.tsx"} <= copied
    assert "credits/Credits.tsx" in copied
    assert "README.md" in copied, "the modules must arrive with the note saying not to edit them"


def test_an_unknown_agent_says_which_ones_exist(service):
    with pytest.raises(ReactScaffoldError) as e:
        service.scaffold("nope")
    assert "known" in str(e.value)


def test_it_refuses_to_scaffold_over_an_existing_project(service, tmp_path):
    """An existing app/ is somebody's work, and the files are the only copy of it."""
    (tmp_path / "known" / "app" / "src").mkdir(parents=True)
    (tmp_path / "known" / "app" / "src" / "App.tsx").write_text("mine", encoding="utf-8")

    with pytest.raises(ReactScaffoldError) as e:
        service.scaffold("known")
    assert "confirm_overwrite" in str(e.value)
    assert (tmp_path / "known" / "app" / "src" / "App.tsx").read_text(encoding="utf-8") == "mine"


def test_installed_dependencies_do_not_count_as_somebody_s_work(service, tmp_path):
    """node_modules is 200MB of somebody ELSE's work. Treating it as an existing project makes
    the refusal fire on a directory nobody authored."""
    nm = tmp_path / "known" / "app" / "node_modules" / "react"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("x", encoding="utf-8")

    assert service.scaffold("known").written  # does not raise


# --------------------------------------------------------------------------- the instruction


