"""The app template Agent Builder hands to every agent it builds — and the tool that copies it.

Why a template exists at all: every UI written from a blank file got the same protocol details
wrong, and both failures are invisible at runtime — the socket connects, the console is clean,
and the screen never updates. Prose describing the right shape is read once and competes with
the model's priors. A file copied onto disk does not compete with anything.

That only holds while the template is CORRECT. The moment it drifts, the mechanism inverts:
instead of one broken agent, every agent built from it is broken the same way. So the template
is held to the checks it exists to enforce —

  * `UiRules` — the same validator that reads a GENERATED ui/. Zero findings, or it is
    shipping the defect it was written to prevent.
  * `node --check` — it must parse.
  * every `getElementById` must have a matching element in index.html, or the app throws on
    boot and takes every other control with it. That bug class was caught by hand once; this
    is the automation of that catch.
"""

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.domain.events import APP_FACING_EVENTS, MESSAGE_UPDATE_KINDS
from agent_runtime.presentation.gateway import APP_SCOPED_METHODS

from agent_authoring.application.scaffold_ui_service import ScaffoldError, ScaffoldUiService
from agent_authoring.domain.ui_rules import UiRules
from agent_authoring.domain.ui_template import CHAT_APP, UiTemplates

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "agents" / "agent-builder"
TEMPLATE_ROOT = BUILDER / "skills" / "build-agent" / "templates"
BORROW_ROOT = BUILDER / "ui"
CHAT_APP_DIR = TEMPLATE_ROOT / CHAT_APP.id

RULES = UiRules(
    events=APP_FACING_EVENTS,
    kinds=MESSAGE_UPDATE_KINDS,
    methods=frozenset(APP_SCOPED_METHODS),
    sdk_methods=frozenset(),
)


def _js_sources() -> dict[str, str]:
    """The template's own JS, keyed the way a validator sees it inside an agent."""
    return {
        f"ui/{rel}": (CHAT_APP_DIR / rel).read_text(encoding="utf-8")
        for rel in CHAT_APP.files
        if rel.endswith(".js")
    }


# ── the template is on disk and complete ────────────────────────────────────
def test_every_file_the_registry_promises_exists():
    """The registry is what the tool copies from. A name here with no file behind it is a
    scaffold that half-succeeds — which looks scaffolded and 404s in the window."""
    for rel in CHAT_APP.files:
        assert (CHAT_APP_DIR / rel).is_file(), f"template missing {rel}"


def test_the_borrowed_files_come_from_the_live_ui():
    """`md.js` and the SDK are taken from Agent Builder's OWN ui/ rather than copied into the
    template, so there is exactly one of each in the product. The SDK especially: a stale copy
    under templates/ would talk a protocol the daemon no longer speaks."""
    for rel in CHAT_APP.borrowed:
        assert (BORROW_ROOT / rel).is_file(), f"cannot borrow {rel} — not in agent-builder/ui/"
        assert not (CHAT_APP_DIR / rel).exists(), (
            f"{rel} is duplicated into the template — that is the drift this avoids"
        )


def test_the_readme_is_one_of_the_files_it_ships():
    assert CHAT_APP.readme in CHAT_APP.files


# ── the template passes the checks it exists to enforce ─────────────────────
def test_the_template_has_no_ui_rule_findings():
    findings = RULES.check(None, {}, [], _js_sources())
    assert not findings, "the template itself trips the validator:\n" + "\n".join(
        f"  [{f.level}] {f.path}: {f.code} — {f.message}" for f in findings
    )


@pytest.mark.parametrize("rel", [r for r in CHAT_APP.files if r.endswith(".js")])
def test_the_template_js_parses(rel):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    proc = subprocess.run(
        [node, "--check", str(CHAT_APP_DIR / rel)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


def test_every_element_it_reaches_for_exists_in_the_markup():
    """`getElementById('x')` where index.html has no `x` returns null, and the next property
    access throws — inside an IIFE that runs at boot, so ONE missing id kills every control on
    the page. Cheap to check, invisible until someone opens the window."""
    import re

    html = (CHAT_APP_DIR / "index.html").read_text(encoding="utf-8")
    declared = set(re.findall(r'\bid="([^"]+)"', html))
    # ids created by JS at runtime rather than declared in the markup
    made_at_runtime = {"hero", "suggests", "setMsg", "setSave"}

    missing: dict[str, set[str]] = {}
    for rel, src in _js_sources().items():
        asked = set(re.findall(r"""\$\(\s*['"]([\w-]+)['"]\s*\)""", src))
        asked |= set(re.findall(r"""getElementById\(\s*['"]([\w-]+)['"]""", src))
        gap = asked - declared - made_at_runtime
        if gap:
            missing[rel] = gap
    assert not missing, f"ids reached for but never declared: {missing}"


def test_it_hardcodes_no_agent_id():
    """The daemon forces this window's own agent onto every request, so an id written into the
    page is a second copy to keep in sync with agent.toml — and naming a SPECIFIC agent would
    be worse: a user's fresh install has none of the agents in this checkout."""
    for rel, src in _js_sources().items():
        for banned in ("agent-builder", "weather", "inbox-triage", "expense-summarizer"):
            assert banned not in src, f"{rel} names the agent '{banned}'"


def test_it_teaches_the_two_mistakes_that_actually_shipped():
    """The template is also documentation — it is read far more often than this test suite."""
    chat = (CHAT_APP_DIR / "chat.js").read_text(encoding="utf-8")
    assert "payload.event.type" in chat
    assert "message_delta" in chat, "name the event that does NOT exist, or it gets reinvented"
    readme = (CHAT_APP_DIR / CHAT_APP.readme).read_text(encoding="utf-8")
    assert "validate_agent" in readme


def test_the_settings_page_stays_narrow():
    """config.set changes the DAEMON, which every agent shares. A downloaded agent whose
    settings page offers the daemon's port or state directory is offering to break the whole
    install from inside a package the user trusted for one job."""
    src = (CHAT_APP_DIR / "settings.js").read_text(encoding="utf-8")
    for machine_wide in ("'port'", "'host'", "'state_dir'", "'agents_dir'", "'workspace'"):
        assert f"key: {machine_wide}" not in src, f"{machine_wide} is machine-wide plumbing"
    # and the one that justifies the page existing at all
    assert "secrets: true" in src, "BYOK is the reason this page ships"


def test_the_settings_page_lets_an_agent_override_the_daemon():
    """Every agent built from this template ships the per-agent layer. Without it, a user with
    two agents can only give them one brain between them."""
    src = (CHAT_APP_DIR / "settings.js").read_text(encoding="utf-8")
    assert "override_default" in src, "the flag the daemon's resolver reads"
    assert "agents." in src, "edits must address config.agents.<id>, the key the daemon exposes"
    assert "cost_efficiency" in src


def test_the_settings_page_never_writes_a_dotted_key_to_the_daemon():
    """config.set whitelists TOP-LEVEL keys, so `agents.<id>.model` must arrive as one nested
    `agents` object. Sending the dotted string looks right on screen and is silently dropped —
    the exact failure mode this page exists to end."""
    src = (CHAT_APP_DIR / "settings.js").read_text(encoding="utf-8")
    assert "setPath(" in src, "nested writes, not dotted patch keys"
    # the patch is built by diffing top-level keys of the draft, never by key-path
    assert "Object.keys(draft)" in src


def test_api_keys_are_never_per_agent():
    """One shared .env. The secrets group must stay outside the agent-scoped block, or the same
    key ends up copied per agent with no defined precedence."""
    src = (CHAT_APP_DIR / "settings.js").read_text(encoding="utf-8")
    secrets_at = src.index("secrets: true")
    group_start = src.rindex("{", 0, secrets_at)
    assert "agent: true" not in src[group_start:secrets_at]


# ── the tool that copies it ─────────────────────────────────────────────────
class FakeReader:
    """Stands in for AgentDirReader: id -> directory, and nothing else."""

    def __init__(self, dirs: dict[str, Path]):
        self._dirs = dirs

    def agent_dir(self, agent_id):
        return self._dirs.get(agent_id)

    def known_ids(self):
        return sorted(self._dirs)


@pytest.fixture
def service(tmp_path):
    (tmp_path / "target").mkdir()
    reader = FakeReader({"target": tmp_path / "target"})
    return ScaffoldUiService(reader, UiTemplates(), TEMPLATE_ROOT, BORROW_ROOT)


def test_it_writes_a_whole_app(service, tmp_path):
    res = service.scaffold("target")
    assert res.written == sorted(CHAT_APP.all_files)
    for rel in CHAT_APP.all_files:
        assert (tmp_path / "target" / "ui" / rel).is_file()
    # the SDK arrives byte-for-byte, not paraphrased
    assert (tmp_path / "target" / "ui" / "vendor" / "agentd-client.js").read_bytes() == (
        BORROW_ROOT / "vendor" / "agentd-client.js"
    ).read_bytes()


def test_the_result_points_at_the_readme(service):
    assert service.scaffold("target").readme_path == "ui/README.md"


def test_it_refuses_to_scaffold_over_an_existing_app(service, tmp_path):
    ui = tmp_path / "target" / "ui"
    ui.mkdir()
    (ui / "app.js").write_text("// six months of someone's work", encoding="utf-8")

    with pytest.raises(ScaffoldError) as e:
        service.scaffold("target")
    assert "REFUSING" in str(e.value)
    assert "app.js" in str(e.value), "name what would be lost, or the refusal is unactionable"
    # and it really did not touch it
    assert (ui / "app.js").read_text(encoding="utf-8").startswith("// six months")


def test_confirm_overwrite_proceeds_and_names_what_it_replaced(service, tmp_path):
    ui = tmp_path / "target" / "ui"
    ui.mkdir()
    (ui / "app.js").write_text("// old", encoding="utf-8")

    res = service.scaffold("target", confirm_overwrite=True)
    assert "app.js" in res.replaced
    assert "// old" not in (ui / "app.js").read_text(encoding="utf-8")


def test_a_file_the_previous_app_had_but_the_template_lacks_is_not_reported_as_replaced(
    service, tmp_path
):
    """`replaced` must mean OVERWRITTEN, not "was there before". A stale file the scaffold
    leaves alone is still on disk, and claiming it was replaced hides that."""
    ui = tmp_path / "target" / "ui"
    ui.mkdir()
    (ui / "legacy.js").write_text("// still here afterwards", encoding="utf-8")

    res = service.scaffold("target", confirm_overwrite=True)
    assert "legacy.js" not in res.replaced
    assert (ui / "legacy.js").is_file()


def test_an_unknown_agent_says_which_ones_exist(service):
    with pytest.raises(ScaffoldError) as e:
        service.scaffold("nope")
    assert "target" in str(e.value)


def test_an_unknown_template_lists_the_real_ones(service):
    with pytest.raises(ScaffoldError) as e:
        service.scaffold("target", "fancy-dashboard")
    assert CHAT_APP.id in str(e.value)


def test_an_incomplete_template_fails_loudly_and_writes_nothing(tmp_path):
    """Half a scaffold is worse than none: it looks done, and the gap only shows up as a 404
    in the window. So the sources are resolved before anything is written."""
    (tmp_path / "target").mkdir()
    empty = tmp_path / "templates"
    (empty / CHAT_APP.id).mkdir(parents=True)
    svc = ScaffoldUiService(
        FakeReader({"target": tmp_path / "target"}), UiTemplates(), empty, BORROW_ROOT
    )
    with pytest.raises(ScaffoldError) as e:
        svc.scaffold("target")
    assert "incomplete" in str(e.value)
    assert not (tmp_path / "target" / "ui").exists(), "it wrote files before failing"


# ── the instructions point at it ────────────────────────────────────────────
# A tool nobody is told to call is a tool nobody calls. Ranked strongest-first, this is the
# AGENTS.md rule (present every turn) and then the skill (read once at build time).
def test_the_standing_rules_say_never_hand_write_a_ui():
    md = (BUILDER / "AGENTS.md").read_text(encoding="utf-8")
    assert "scaffold_ui" in md
    assert "blank file" in md.lower()


def test_the_skill_sends_you_to_the_tool_before_the_reference():
    skill = (BUILDER / "skills" / "build-agent" / "SKILL.md").read_text(encoding="utf-8")
    assert "scaffold_ui" in skill
    ui_section = skill.index("## ui/ — the agent's own app")
    assert skill.index("scaffold_ui", ui_section) < skill.index("index.html`", ui_section), (
        "the tool must come before the file-by-file reference, or the reference gets retyped"
    )
