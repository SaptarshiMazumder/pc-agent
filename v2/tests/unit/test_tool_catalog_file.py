"""`<state_dir>/tools.json` — the list of tools an agent can be granted, on disk.

Agent Builder fills in `[tools] allow` for every agent it builds, choosing from ~50 tools. There
was no list to read: the catalog is assembled at boot by importing every plugin and letting each
register itself. So it chose from recall — and when it could not recall a tool that already did
the job, it wrote a private one instead, which then had to satisfy every sandbox restriction the
existing tool was exempt from.

The file is DERIVED, never maintained, so it cannot disagree with the daemon it describes.

The property worth protecting is freshness. `create_tool` hot-adds without a restart and MCP
servers connect after discovery, so a boot-only snapshot would be stale for exactly the tool
someone just made and would look for first.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.application.services.agent_service import AgentService
from agent_runtime.infrastructure import tool_catalog_file


class _Tool:
    def __init__(self, name, description="", source=None, plugin_id=None):
        self.name = name
        self.description = description
        if source:
            self.source = source
        if plugin_id:
            self._plugin_id = plugin_id


def _written(tmp_path) -> dict:
    return json.loads((tmp_path / tool_catalog_file.FILENAME).read_text(encoding="utf-8"))


# ── the shape ───────────────────────────────────────────────────────────────
def test_it_records_name_summary_and_where_it_came_from(tmp_path):
    tool_catalog_file.write(tmp_path, [_Tool("show_files", "Show the files you produced.",
                                             source="plugin:show")])
    row = _written(tmp_path)["tools"][0]
    assert row == {"name": "show_files", "summary": "Show the files you produced.",
                   "source": "plugin:show"}


def test_the_summary_is_one_line(tmp_path):
    """The real descriptions are paragraphs — create_agent's runs to 25 lines. A file that costs
    more to read than it saves does not get read."""
    tool_catalog_file.write(tmp_path, [_Tool("x", "First line.\nSecond line.\nThird.")])
    assert _written(tmp_path)["tools"][0]["summary"] == "First line."


def test_a_very_long_first_line_is_clipped(tmp_path):
    tool_catalog_file.write(tmp_path, [_Tool("x", "y" * 500)])
    assert len(_written(tmp_path)["tools"][0]["summary"]) <= 200


def test_the_source_is_derived_when_not_tagged(tmp_path):
    tool_catalog_file.write(tmp_path, [
        _Tool("a", plugin_id="core_fs"),
        _Tool("srv__thing"),          # an MCP tool is namespaced by its server
        _Tool("plain"),
    ])
    got = {r["name"]: r["source"] for r in _written(tmp_path)["tools"]}
    assert got == {"a": "plugin:core_fs", "srv__thing": "mcp:srv", "plain": "internal"}


def test_it_is_sorted_and_deduped(tmp_path):
    """Sorted so a diff between two boots shows what changed rather than import order."""
    tool_catalog_file.write(tmp_path, [_Tool("b"), _Tool("a"), _Tool("b")])
    assert [r["name"] for r in _written(tmp_path)["tools"]] == ["a", "b"]


def test_it_says_not_to_edit_it(tmp_path):
    """It is overwritten. Someone will find it and try."""
    tool_catalog_file.write(tmp_path, [_Tool("a")])
    assert "Do not edit" in _written(tmp_path)["note"]


# ── failure is loud, never fatal ────────────────────────────────────────────
def test_an_unwritable_location_returns_none_rather_than_raising(tmp_path, caplog):
    """Advisory: a daemon that will not start because a convenience file could not be written
    is worse than the gap. But it is LOGGED — a catalog that is silently absent looks exactly
    like a machine with no tools, which is the confusion this exists to end."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    with caplog.at_level("ERROR"):
        assert tool_catalog_file.write(blocker / "state", [_Tool("a")]) is None
    assert any("tool catalog" in r.message for r in caplog.records)


def test_a_reader_never_sees_a_half_written_file(tmp_path):
    """Written to a temp path and renamed, like the installed-bundle ledger next door."""
    tool_catalog_file.write(tmp_path, [_Tool("a")])
    assert not list(tmp_path.glob("*.tmp")), "the temp file must not survive"
    tool_catalog_file.write(tmp_path, [_Tool("b")])
    assert [r["name"] for r in _written(tmp_path)["tools"]] == ["b"], "and it replaces, not appends"


# ── freshness: the whole point ──────────────────────────────────────────────
def test_adding_a_tool_after_boot_rewrites_the_file(tmp_path):
    """`create_tool` hot-adds and MCP connects after startup — both land in add_tools. A
    boot-only snapshot would omit the tool that was just created, which is the one anyone
    would go looking for."""
    seen = []
    svc = AgentService(
        engine=None, tools=[_Tool("read")], registry=None, make_session=None, build_prompt=None,
        on_catalog_change=lambda tools: seen.append([t.name for t in tools]),
    )
    svc.add_tools([_Tool("brand_new")])
    assert seen == [["read", "brand_new"]]


def test_a_broken_hook_never_breaks_the_hot_add(tmp_path):
    """The tool still has to become callable. Bookkeeping is not allowed to take that down."""
    def boom(_tools):
        raise OSError("disk full")

    svc = AgentService(engine=None, tools=[], registry=None, make_session=None,
                       build_prompt=None, on_catalog_change=boom)
    svc.add_tools([_Tool("still_works")])
    assert [t.name for t in svc._tools] == ["still_works"]


def test_no_hook_at_all_is_fine(tmp_path):
    svc = AgentService(engine=None, tools=[], registry=None, make_session=None, build_prompt=None)
    svc.add_tools([_Tool("a")])
    assert [t.name for t in svc._tools] == ["a"]


# ── the skill points at it ──────────────────────────────────────────────────
def test_the_skill_tells_it_to_read_the_catalog():
    """A file nobody is told about is a file nobody reads — which is where this started."""
    skill = (Path(__file__).resolve().parents[2] / "agents" / "agent-builder" / "skills"
             / "build-agent" / "SKILL.md").read_text(encoding="utf-8")
    assert tool_catalog_file.FILENAME in skill
    assert "before writing a private tool" in skill.lower()
