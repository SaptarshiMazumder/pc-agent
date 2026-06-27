"""Tool cards — a tool's self-description (summary + instruction block) in Markdown, loaded
from tools/<name>.md, replacing the hardcoded TOOL_SUMMARIES dict + if-name prompt blocks."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.cards import load_cards


def _card(tmp_path, name, text):
    (tmp_path / f"{name}.md").write_text(text, encoding="utf-8")


def test_loads_summary_and_body(tmp_path):
    _card(tmp_path, "update_plan", "---\nsummary: Track short work plan\n---\n## Planning\nDo it.")
    c = load_cards(tmp_path)["update_plan"]
    assert c.summary == "Track short work plan" and c.prompt == "## Planning\nDo it."


def test_summary_only_card_has_empty_prompt(tmp_path):
    _card(tmp_path, "read", "---\nsummary: Read file contents\n---\n")
    cards = load_cards(tmp_path)
    assert cards["read"].summary == "Read file contents" and cards["read"].prompt == ""


def test_subfolder_card(tmp_path):
    d = tmp_path / "verify_answer"
    d.mkdir()
    (d / "card.md").write_text("---\nsummary: Review\n---\n## Verify\nCheck.", encoding="utf-8")
    cards = load_cards(tmp_path)
    assert cards["verify_answer"].summary == "Review" and "## Verify" in cards["verify_answer"].prompt


def test_missing_dir_is_empty():
    assert load_cards("/no/such/dir/xyz") == {}


def test_card_declares_scripts_data_and_resolves(tmp_path):
    (tmp_path / "helper.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "ref.json").write_text("{}", encoding="utf-8")
    _card(tmp_path, "mytool", "---\nsummary: T\nscripts: helper.py\ndata: ref.json\n---\nbody")
    c = load_cards(tmp_path)["mytool"]
    assert c.scripts == ("helper.py",) and c.data == ("ref.json",)
    assert c.resource("helper.py") == tmp_path / "helper.py" and c.resource("helper.py").exists()


def test_card_with_no_assets_has_empty_tuples(tmp_path):
    _card(tmp_path, "x", "---\nsummary: S\n---\n")
    c = load_cards(tmp_path)["x"]
    assert c.scripts == () and c.data == () and c.resource("anything") == tmp_path / "anything"


def test_real_repo_cards_present():
    # the actual tools/ folder ships the migrated cards
    cards = load_cards()  # default <V2_ROOT>/tools
    assert cards["update_plan"].summary == "Track short work plan"
    assert "## Planning" in cards["update_plan"].prompt
    assert cards["verify_answer"].summary.startswith("Review your draft answer")
    assert "## Verify Before You Send" in cards["verify_answer"].prompt
    assert cards["read"].summary == "Read file contents" and cards["read"].prompt == ""
