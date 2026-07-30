"""Plugin discovery + native loader: a tool dropped into the plugins folder joins the catalog,
the per-plugin load gate skips (and never imports) a disabled plugin, and a broken plugin is
skipped without raising. End-to-end against a real temp plugins dir + a real plugin module."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.infrastructure.plugins import (
    discover_plugin_contributions,
    discover_plugin_tools,
    load_manifest,
)

# a minimal, self-contained native plugin: a duck-typed Tool + a register(api, ctx) entry.
PLUGIN_MOD = """
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
"""


def _make_plugin(
    tmp_path, pid="myplugin", mod="agentd_demo_plugin_mod", enabled=None, body=PLUGIN_MOD
):
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
    assert "agentd_off_plugin_mod" not in sys.modules  # gated BEFORE import -> deps never load


def test_manifest_enabled_false_is_off_by_default(tmp_path):
    pdir = _make_plugin(tmp_path, pid="m_off", mod="agentd_moff_mod", enabled=False)
    assert discover_plugin_tools(_cfg(pdir)) == []


def test_config_can_reenable_a_manifest_default_off(tmp_path):
    pdir = _make_plugin(tmp_path, pid="m_off2", mod="agentd_moff2_mod", enabled=False)
    tools = discover_plugin_tools(_cfg(pdir, plugins={"m_off2": True}))
    assert [t.name for t in tools] == ["hello_plugin"]


def test_broken_plugin_skipped_not_raised(tmp_path):
    pdir = _make_plugin(
        tmp_path,
        pid="broken",
        mod="agentd_broken_mod",
        body="import a_package_that_does_not_exist_xyz\n",
    )
    assert discover_plugin_tools(_cfg(pdir)) == []  # import error -> skipped, no raise


def test_empty_or_missing_dir_returns_empty():
    assert discover_plugin_tools(_cfg("")) == []  # empty -> no CWD scan
    assert discover_plugin_tools(_cfg("/no/such/dir/xyz")) == []


def test_load_manifest_rejects_bad_kind(tmp_path):
    p = tmp_path / "plugin.toml"
    p.write_text('id = "x"\nname = "X"\nkind = "weird"\n', encoding="utf-8")
    assert load_manifest(p) is None


def test_load_manifest_requires_entry_for_native(tmp_path):
    p = tmp_path / "plugin.toml"
    p.write_text('id = "x"\nname = "X"\nkind = "native"\n', encoding="utf-8")  # no entry
    assert load_manifest(p) is None


# ── contributions: tools + prompt sections + mcp servers ─────────────────────

_PLUGIN_WITH_SECTION = """
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
"""


def test_native_plugin_contributes_tools_and_prompt_sections(tmp_path):
    pdir = _make_plugin(tmp_path, pid="hp", mod="agentd_hp_mod", body=_PLUGIN_WITH_SECTION)
    tools, sections, servers, skills = discover_plugin_contributions(_cfg(pdir))
    assert [t.name for t in tools] == ["hello2"]
    assert servers == [] and skills == [] and len(sections) == 1
    assert sections[0]([SimpleNamespace(name="hello2")], None, None) == "## Hello block"


def test_mcp_plugin_contributes_a_server_config(tmp_path):
    d = tmp_path / "plugins" / "myremote"
    d.mkdir(parents=True)
    (d / "plugin.toml").write_text(
        'id = "myremote"\nname = "R"\nkind = "mcp"\n[mcp]\ncommand = ["uvx", "x"]\n',
        encoding="utf-8",
    )
    tools, sections, servers, skills = discover_plugin_contributions(_cfg(tmp_path / "plugins"))
    assert tools == [] and sections == []
    assert (
        len(servers) == 1 and servers[0].name == "myremote" and servers[0].command == ["uvx", "x"]
    )


def test_plugin_bundled_skills_dir_is_discovered(tmp_path):
    pdir = _make_plugin(tmp_path, pid="sk", mod="agentd_sk_mod")
    skdir = tmp_path / "plugins" / "sk" / "skills" / "demo"
    skdir.mkdir(parents=True)
    (skdir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: a demo skill\n---\nbody", encoding="utf-8"
    )
    _, _, _, skill_dirs = discover_plugin_contributions(_cfg(pdir))
    assert len(skill_dirs) == 1 and skill_dirs[0].name == "skills"


# ── bundled scripts/data: declaration + the ctx.resource() resolver ──────────

_PLUGIN_USES_RESOURCE = """
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
"""


def test_manifest_declares_scripts_and_data(tmp_path):
    d = tmp_path / "plugins" / "dp"
    d.mkdir(parents=True)
    (d / "helper.py").write_text("x = 1", encoding="utf-8")
    (d / "ref.json").write_text("{}", encoding="utf-8")
    (d / "plugin.toml").write_text(
        'id = "dp"\nname = "D"\nkind = "native"\nentry = "m:register"\n'
        'scripts = ["helper.py"]\ndata = ["ref.json"]\n',
        encoding="utf-8",
    )
    m = load_manifest(d / "plugin.toml")
    assert m.scripts == ("helper.py",) and m.data == ("ref.json",)


def test_plugin_reads_bundled_data_via_ctx_resource(tmp_path):
    pdir = _make_plugin(tmp_path, pid="rp", mod="agentd_rp_mod", body=_PLUGIN_USES_RESOURCE)
    (tmp_path / "plugins" / "rp" / "ref.txt").write_text("HELLO", encoding="utf-8")
    tools, _, _, _ = discover_plugin_contributions(_cfg(pdir))
    assert tools and tools[0].bundled == "HELLO"  # the plugin resolved + read its own data


# ── dependency injection: a plugin tool gets the same singletons the built-ins do ─────────────

_PLUGIN_USES_DEPS = """
class DepTool:
    name = "dep_tool"
    description = "uses injected deps"
    parameters = {"type": "object"}
    label = "Dep"
    concurrency = "parallel"
    def __init__(self, browser, task_store):
        self.browser, self.task_store = browser, task_store
    async def execute(self, *a, **k):
        return None

def register(api, ctx):
    api.register_tool(DepTool(ctx.browser, ctx.task_store))   # pulls deps off the context
"""


def test_plugin_receives_injected_deps(tmp_path):
    pdir = _make_plugin(tmp_path, pid="dep", mod="agentd_dep_mod", body=_PLUGIN_USES_DEPS)
    browser, store = object(), object()
    tools, _, _, _ = discover_plugin_contributions(
        _cfg(pdir), {"browser": browser, "task_store": store}
    )
    assert tools[0].browser is browser and tools[0].task_store is store


def test_unknown_deps_ignored_absent_ones_are_none(tmp_path):
    pdir = _make_plugin(tmp_path, pid="dep2", mod="agentd_dep2_mod", body=_PLUGIN_USES_DEPS)
    # an unknown handle is dropped (forward-compat), and a dep that wasn't provided is None
    tools, _, _, _ = discover_plugin_contributions(_cfg(pdir), {"bogus_handle": object()})
    assert tools[0].browser is None and tools[0].task_store is None


# ── 4-gate loading: compatibility (os/bins/env) + entitlement (the distribution seam) ─────────


def _append_toml(tmp_path, pid, extra):
    toml = tmp_path / "plugins" / pid / "plugin.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + extra, encoding="utf-8")


def test_manifest_parses_requires(tmp_path):
    d = tmp_path / "plugins" / "rq"
    d.mkdir(parents=True)
    (d / "plugin.toml").write_text(
        'id = "rq"\nname = "R"\nkind = "native"\nentry = "m:register"\n'
        '[requires]\nos = ["Linux", "Windows"]\nbins = ["git"]\nenv = ["HOME"]\n',
        encoding="utf-8",
    )
    m = load_manifest(d / "plugin.toml")
    assert m.requires == {"os": ["linux", "windows"], "bins": ["git"], "env": ["HOME"]}


def test_incompatible_os_skips_plugin(tmp_path):
    pdir = _make_plugin(tmp_path, pid="osx", mod="agentd_osx_mod")
    _append_toml(tmp_path, "osx", '[requires]\nos = ["plan9"]\n')  # never the current platform
    assert discover_plugin_tools(_cfg(pdir)) == []


def test_missing_bin_skips_plugin(tmp_path):
    pdir = _make_plugin(tmp_path, pid="bn", mod="agentd_bn_mod")
    _append_toml(tmp_path, "bn", '[requires]\nbins = ["definitely_absent_bin_zzz_123"]\n')
    assert discover_plugin_tools(_cfg(pdir)) == []


def test_compatible_requires_still_loads(tmp_path):
    pdir = _make_plugin(tmp_path, pid="ok", mod="agentd_ok_mod")
    _append_toml(tmp_path, "ok", '[requires]\nenv = ["PATH"]\n')  # PATH is always set
    assert [t.name for t in discover_plugin_tools(_cfg(pdir))] == ["hello_plugin"]


def test_entitlement_can_deny_and_allow(tmp_path):
    pdir = _make_plugin(tmp_path, pid="ent", mod="agentd_ent_mod")

    class Deny:
        def is_entitled(self, manifest):
            return False

    class Allow:
        def is_entitled(self, manifest):
            return manifest.id == "ent"

    assert discover_plugin_contributions(_cfg(pdir), None, Deny())[0] == []
    assert [t.name for t in discover_plugin_contributions(_cfg(pdir), None, Allow())[0]] == [
        "hello_plugin"
    ]


def test_entitlement_fails_open_on_error(tmp_path):
    pdir = _make_plugin(tmp_path, pid="ent2", mod="agentd_ent2_mod")

    class Boom:
        def is_entitled(self, manifest):
            raise RuntimeError("policy bug")

    # a broken policy must not silently disable tools — it fails OPEN (allowed), logged.
    assert [t.name for t in discover_plugin_contributions(_cfg(pdir), None, Boom())[0]] == [
        "hello_plugin"
    ]


def test_default_entitlement_allows_all(tmp_path):
    from agent_runtime.infrastructure.plugins import AllowAllEntitlement

    pdir = _make_plugin(tmp_path, pid="ent3", mod="agentd_ent3_mod")
    tools = discover_plugin_contributions(_cfg(pdir), None, AllowAllEntitlement())[0]
    assert [t.name for t in tools] == ["hello_plugin"]
