"""The React starter, and the instruction to learn from the samples rather than from a template.

TWO FAILURES ARE PINNED HERE, and both are silent for the author and fatal for everyone else.

The first is packaging: a React app reaches the SDK through `@agentd/client`, and in this repo the
samples resolve that with a relative `file:` path into `clients/sdk-js`. An agent scaffolded into
a user's own agents directory has nothing at that path — `npm install` fails and the app can never
be built by anyone who did not write it. The starter therefore carries the SDK.

The second is pedagogy: the skill must send the model to the samples as a SET. Naming one turns a
reference into a mould, and every agent after it becomes a recolour of whichever sample got named.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_authoring.application.scaffold_react_app_service import (
    STARTER_FILES,
    ReactScaffoldError,
    ScaffoldReactAppService,
)
from agent_authoring.bundle_layout import BundleLayout

ROOT = Path(__file__).resolve().parents[2]
STARTER = BundleLayout.BORROW_ROOT / "react"
SKILL = ROOT / "agents" / "agent-builder" / "skills" / "build-agent" / "SKILL.md"
SAMPLES = ROOT / "agents" / "samples"


# --------------------------------------------------------------------------- the starter


@pytest.mark.parametrize("rel", STARTER_FILES)
def test_every_file_the_starter_promises_exists(rel):
    """A missing piece is a build error naming nothing useful, several steps later."""
    assert (STARTER / rel).is_file(), (
        f"{rel} is missing from {STARTER}. For vendor/, run `npm run build` in clients/sdk-js."
    )


def test_the_starter_does_not_depend_on_the_sdk_package():
    """THE WHOLE POINT. A dependency on '@agentd/client' resolves only inside this repo, so an
    agent that declares one cannot be built by whoever receives it."""
    pkg = json.loads((STARTER / "package.json").read_text(encoding="utf-8"))
    declared = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    assert "@agentd/client" not in declared, (
        "the starter declares @agentd/client — that path exists only in this repo; "
        "the SDK is vendored into vendor/ and aliased instead"
    )


def test_the_sdk_is_aliased_for_both_the_bundler_and_the_compiler():
    """Two resolvers, two configs. Vite alone leaves TypeScript reporting 'cannot find module'
    on every file that imports the SDK; tsconfig alone builds nothing."""
    assert "@agentd/client" in (STARTER / "vite.config.ts").read_text(encoding="utf-8")
    tsconfig = (STARTER / "tsconfig.json").read_text(encoding="utf-8")
    assert '"@agentd/client"' in tsconfig


def test_the_vendored_sdk_is_the_real_build():
    """A hand-maintained copy drifts from the daemon it talks to. This one is written by the
    SDK's own build (clients/sdk-js/scripts/vendor.mjs), so it cannot."""
    vendored = (STARTER / "vendor" / "agentd-client.js").read_bytes()
    built = (ROOT / "clients" / "sdk-js" / "dist" / "index.js").read_bytes()
    assert vendored == built, "vendor/agentd-client.js is stale — run `npm run build` in clients/sdk-js"


def test_the_build_writes_where_the_daemon_reads():
    """app/ is source and ui/ is what ships. Point outDir anywhere else and the window 404s."""
    config = (STARTER / "vite.config.ts").read_text(encoding="utf-8")
    assert "'../ui'" in config
    # Served under /apps/<id>/, so absolute asset URLs request the daemon root and every chunk
    # 404s — a blank window with a clean console.
    assert "base: './'" in config


COMMON = STARTER.parent.parent / "_common"


def _same(a, b) -> bool:
    """Compared as normalised text: git checks these out CRLF on Windows and LF elsewhere, so a
    byte comparison would fail on the platform rather than on the content."""
    def norm(p):
        return p.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")

    return norm(a) == norm(b)


def test_the_starter_ships_only_the_mandatory_source_file():
    """What the window should BE is a judgement about the agent, made from the samples — a copied
    src/ would be a fourth opinion competing with them.

    WHERE SIGN-IN HAPPENS IS NOT A JUDGEMENT, and getting it wrong (render first, sign in later)
    is a mistake to design out rather than document. So exactly one file ships, and the list is
    asserted EXACTLY: a second one appearing here means somebody started making judgements on the
    author's behalf. Everything shared lives in _common/, which is a different tier."""
    assert sorted(p.name for p in (STARTER / "src").iterdir()) == ["main.tsx"]


def test_the_shipped_entry_signs_in_before_it_renders():
    """A starter that shipped a main.tsx WITHOUT this would be worse than shipping none — it looks
    like the question was already handled."""
    main = (STARTER / "src" / "main.tsx").read_text(encoding="utf-8")

    assert "signInFirst" in main
    assert main.index("signInFirst") < main.index("root.render"), "sign in before the render"
    # THROUGH THE COMMON MODULE, not by reaching for the SDK here. One place per agent knows how
    # signing in works, and it is the copied module every other agent has too.
    assert "./common/auth/SignIn" in main


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
    return ScaffoldReactAppService(FakeReader(tmp_path), STARTER)


def test_it_writes_a_buildable_project(service, tmp_path):
    result = service.scaffold("known")

    for rel in STARTER_FILES:
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


def test_the_skill_sends_the_model_to_the_samples():
    text = SKILL.read_text(encoding="utf-8")
    assert "agents/samples/" in text, "the skill never points at the samples"
    assert SAMPLES.is_dir(), f"the skill points at {SAMPLES}, which does not exist"


def test_the_skill_names_no_individual_sample():
    """A named sample is a mould, and every agent after it becomes a recolour of that one. The
    instruction is to read them as a SET and judge — which also survives adding a third."""
    text = SKILL.read_text(encoding="utf-8").lower()
    for sample in (p.name for p in SAMPLES.iterdir() if (p / "agent.toml").is_file()):
        assert sample.lower() not in text, (
            f"the skill names the sample '{sample}' — that teaches one answer, not the judgement"
        )


def test_the_skill_says_to_read_more_than_one():
    text = SKILL.read_text(encoding="utf-8").lower()
    assert "more than one" in text


def test_the_skill_warns_about_the_sdk_path_the_samples_use():
    """The one line a model WILL copy from a sample's package.json, and the one that breaks the
    build for everybody but its author."""
    text = SKILL.read_text(encoding="utf-8")
    assert "@agentd/client" in text
    assert "delete that line" in text.lower()
