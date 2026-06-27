"""Plugin discovery + native loader: a tool dropped into the plugins folder joins the catalog,
the per-plugin load gate skips (and never imports) a disabled plugin, and a broken plugin is
skipped without raising. End-to-end against a real temp plugins dir + a real plugin module."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.plugins import (
    discover_plugin_contributions,
    discover_plugin_tools,
    load_manifest,
)

# a minimal, self-contained native plugin: a duck-typed Tool + a register(api, ctx) entry.
PLUGIN_MOD = '''
class HelloTool:
    name = "hello_plugin"
    description = "says hi"
    parameters = {"type": "object"}
    label = "Hello"
    concurrency = "parallel"
    async def execute(self, call_id, params, abort, on_update=None):
        return None

def register(api, ctx):
    api.register_tool(HelloTool())
'''


def _make_plugin(tmp_path, pid="myplugin", mod="agentd_demo_plugin_mod", enabled=None, body=PLUGIN_MOD):
    d = tmp_path / "plugins" / pid
    d.mkdir(parents=True)
    toml = f'id = "{pid}"\nname = "Demo"\nkind = "native"\nentry = "{mod}:register"\n'
    if enabled is not None:
        toml += f"enabled = {str(enabled).lower()}\n"
    (d / "plugin.toml").write_text(toml, encoding="utf-8")
    (d / f"{mod}.py").write_text(body, encoding="utf-8")
    return tmp_path / "plugins"


def _cfg(plugins_dir, plugins=None):
    return SimpleNamespace(plugins_dir=str(plugins_dir), plugins=plugins or {})


def test_native_plugin_in_dir_joins_catalog(tmp_path):
    pdir = _make_plugin(tmp_path)
    tools = discover_plugin_tools(_cfg(pdir))
    assert [t.name for t in tools] == ["hello_plugin"]


def test_plugin_disabled_by_config_is_never_imported(tmp_path):
    pdir = _make_plugin(tmp_path, pid="off_plugin", mod="agentd_off_plugin_mod")
    tools = discover_plugin_tools(_cfg(pdir, plugins={"off_plugin": False}))
    assert tools == []
    assert "agentd_off_plugin_mod" not in sys.modules        # gated BEFORE import -> deps never load


def test_manifest_enabled_false_is_off_by_default(tmp_path):
    pdir = _make_plugin(tmp_path, pid="m_off", mod="agentd_moff_mod", enabled=False)
    assert discover_plugin_tools(_cfg(pdir)) == []


def test_config_can_reenable_a_manifest_default_off(tmp_path):
    pdir = _make_plugin(tmp_path, pid="m_off2", mod="agentd_moff2_mod", enabled=False)
    tools = discover_plugin_tools(_cfg(pdir, plugins={"m_off2": True}))
    assert [t.name for t in tools] == ["hello_plugin"]


def test_broken_plugin_skipped_not_raised(tmp_path):
    pdir = _make_plugin(tmp_path, pid="broken", mod="agentd_broken_mod",
                        body="import a_package_that_does_not_exist_xyz\n")
    assert discover_plugin_tools(_cfg(pdir)) == []           # import error -> skipped, no raise


def test_empty_or_missing_dir_returns_empty():
    assert discover_plugin_tools(_cfg("")) == []             # empty -> no CWD scan
    assert discover_plugin_tools(_cfg("/no/such/dir/xyz")) == []


def test_load_manifest_rejects_bad_kind(tmp_path):
    p = tmp_path / "plugin.toml"
    p.write_text('id = "x"\nname = "X"\nkind = "weird"\n', encoding="utf-8")
    assert load_manifest(p) is None


def test_load_manifest_requires_entry_for_native(tmp_path):
    p = tmp_path / "plugin.toml"
    p.write_text('id = "x"\nname = "X"\nkind = "native"\n', encoding="utf-8")   # no entry
    assert load_manifest(p) is None


# ── contributions: tools + prompt sections + mcp servers ─────────────────────

_PLUGIN_WITH_SECTION = '''
class HelloTool:
    name = "hello2"
    description = "hi"
    parameters = {"type": "object"}
    label = "H"
    concurrency = "parallel"
    async def execute(self, *a, **k):
        return None

def section(tools, agent, config):
    return "## Hello block" if any(getattr(t, "name", "") == "hello2" for t in tools) else ""

def register(api, ctx):
    api.register_tool(HelloTool())
    api.register_prompt_section(section)
'''


def test_native_plugin_contributes_tools_and_prompt_sections(tmp_path):
    pdir = _make_plugin(tmp_path, pid="hp", mod="agentd_hp_mod", body=_PLUGIN_WITH_SECTION)
    tools, sections, servers = discover_plugin_contributions(_cfg(pdir))
    assert [t.name for t in tools] == ["hello2"]
    assert servers == [] and len(sections) == 1
    assert sections[0]([SimpleNamespace(name="hello2")], None, None) == "## Hello block"


def test_mcp_plugin_contributes_a_server_config(tmp_path):
    d = tmp_path / "plugins" / "myremote"
    d.mkdir(parents=True)
    (d / "plugin.toml").write_text(
        'id = "myremote"\nname = "R"\nkind = "mcp"\n[mcp]\ncommand = ["uvx", "x"]\n', encoding="utf-8")
    tools, sections, servers = discover_plugin_contributions(_cfg(tmp_path / "plugins"))
    assert tools == [] and sections == []
    assert len(servers) == 1 and servers[0].name == "myremote" and servers[0].command == ["uvx", "x"]


# ── bundled scripts/data: declaration + the ctx.resource() resolver ──────────

_PLUGIN_USES_RESOURCE = '''
from pathlib import Path
class T:
    name = "rt"
    description = "hi"
    parameters = {"type": "object"}
    label = "T"
    concurrency = "parallel"
    def __init__(self, data):
        self.bundled = data
    async def execute(self, *a, **k):
        return None

def register(api, ctx):
    data = Path(ctx.resource("ref.txt")).read_text(encoding="utf-8")   # bundled data file
    api.register_tool(T(data))
'''


def test_manifest_declares_scripts_and_data(tmp_path):
    d = tmp_path / "plugins" / "dp"
    d.mkdir(parents=True)
    (d / "helper.py").write_text("x = 1", encoding="utf-8")
    (d / "ref.json").write_text("{}", encoding="utf-8")
    (d / "plugin.toml").write_text(
        'id = "dp"\nname = "D"\nkind = "native"\nentry = "m:register"\n'
        'scripts = ["helper.py"]\ndata = ["ref.json"]\n', encoding="utf-8")
    m = load_manifest(d / "plugin.toml")
    assert m.scripts == ("helper.py",) and m.data == ("ref.json",)


def test_plugin_reads_bundled_data_via_ctx_resource(tmp_path):
    pdir = _make_plugin(tmp_path, pid="rp", mod="agentd_rp_mod", body=_PLUGIN_USES_RESOURCE)
    (tmp_path / "plugins" / "rp" / "ref.txt").write_text("HELLO", encoding="utf-8")
    tools, _, _ = discover_plugin_contributions(_cfg(pdir))
    assert tools and tools[0].bundled == "HELLO"      # the plugin resolved + read its own data
