"""What Agent Builder is TOLD about giving an agent a window.

A tool nobody is told to call is a tool nobody calls, and an instruction naming a tool that no
longer exists is worse than none — the model follows it and fails. So the wording is held to the
same standard as the code, ranked strongest-first: `AGENTS.md` is present on every turn, the skill
is read once at build time.

These guards outlived what they were written for. They used to live in `test_ui_template.py`,
beside the vanilla templates and the tool that copied them. Both were retired when a window became
a React project; this is the half that still guards something.
"""

from __future__ import annotations

from pathlib import Path

V2 = Path(__file__).resolve().parents[2]
BUILDER = V2 / "agents" / "agent-builder"
SKILL = BUILDER / "skills" / "build-agent" / "SKILL.md"


def test_the_standing_rules_say_never_hand_write_a_ui():
    """Every UI written from a blank file got the same protocol details wrong, and both failures
    are invisible at runtime: the socket connects, the console is clean, the screen never
    updates."""
    md = (BUILDER / "AGENTS.md").read_text(encoding="utf-8")
    assert "scaffold_react_app" in md
    assert "blank file" in md.lower()


def test_the_standing_rules_say_to_build_after_editing():
    """`app/` is source and `ui/` is what the daemon serves. A change that is never built is one
    the user reloads and cannot see, while every file they can inspect says the work was done — so
    the instruction belongs in the file present on EVERY turn, not only in the skill."""
    md = (BUILDER / "AGENTS.md").read_text(encoding="utf-8")
    assert "build_app" in md


def test_no_instruction_still_points_at_the_retired_scaffolder():
    """`scaffold_ui` and its vanilla templates are gone. Nothing may offer one as a way to start
    something — and this test earns its keep: it caught a passage that was added while the removal
    was still in progress."""
    for name in ("AGENTS.md", "skills/build-agent/SKILL.md"):
        text = (BUILDER / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if "scaffold_ui" not in line:
                continue
            # Saying it is GONE is the one legitimate mention: a model carrying an older copy of
            # these instructions needs to be told, or it goes looking for the tool.
            assert "gone" in line.lower(), f"{name} still offers scaffold_ui: {line.strip()}"


def test_the_retired_templates_are_not_on_disk():
    """Deleted rather than left lying about. A directory of working vanilla apps is an invitation
    to copy one whatever the instructions say — and the agents that still HAVE a hand-written
    `ui/` carry their own copy, so nothing here was serving them."""
    templates = BUILDER / "skills" / "build-agent" / "templates"
    for retired in ("chat-app", "dashboard-app", "workbench-app"):
        assert not (templates / retired).exists(), f"{retired} is back"
    # `_borrowed/` WENT WITH THEM, later and for the same reason. It held the two-file React
    # starter — superseded by `_skeleton/`, a whole working app — and a shared `md.js` that
    # nothing imported: Agent Builder renders with react-markdown, and the legacy agents that
    # still load `md.js` each carry their own copy in their own `ui/`.
    assert not (templates / "_borrowed").exists(), "the starter is back"
    # And the thing that replaced it is there. A test that only checks for absence passes just as
    # happily when BOTH are gone.
    assert (templates / "_skeleton" / "src" / "App.tsx").is_file()


def test_the_skill_says_the_window_already_exists_before_it_says_anything_else():
    """THE FIRST THING TO KNOW, because it changes what the rest of the section means.

    This used to assert that `scaffold_react_app` was named before the file-by-file reference, so
    the reference would not get retyped from scratch. There is no reference to retype now — the
    window arrives complete — and the failure it guards against moved with it: a model that reads
    the mechanics first starts BUILDING a window instead of editing the one it already has."""
    skill = SKILL.read_text(encoding="utf-8")
    ui = skill.index("## ui/ — the agent's own app")

    starts_with = skill.index("already HAS one", ui)
    for later in ("`build_app`", "vendor/agentd-client.js"):
        assert starts_with < skill.index(later, ui), f"{later} is explained before the window is"
