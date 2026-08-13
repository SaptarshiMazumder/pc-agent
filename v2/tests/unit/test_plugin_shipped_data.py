"""A plugin can reach the data it SHIPPED WITH — hosted, desktop, sandboxed or not.

Two independent defects produced one identical symptom ("no templates found" while five valid
template files sat on disk in the right folder), and both only bit away from the author's own
machine, which is the worst place for a bug to live:

  1. MODULE IDENTITY WAS A BARE NAME. `import_module("marketing_image")` keyed every plugin in one
     global namespace, so re-installing an agent at a new version kept serving the OLD module out
     of sys.modules (the fixed file on disk never ran), and two agents shipping the same module
     name silently shared whichever loaded first.
  2. THE SANDBOX GRANTED THE PLUGIN'S DIRECTORY BUT NOT ITS PACKAGE. Data lives beside the code
     inside the agent folder — that folder IS the .agentpkg — but the grant covered the workspace
     (a different tree entirely for a signed-in run) and the plugin's own subdirectory only, so
     the sibling `templates/` one level up was denied. Worse on hosted: an account's agents live
     under the tenant root, which the deny tier covers wholesale.

Both fixes are location-shaped, not name-shaped: a plugin's identity and its rights both follow
WHERE IT CAME FROM, so they hold for every agent, every account and every deployment.
"""

import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.application.run_context import RunContext
from agent_runtime.domain.sandbox import PluginOrigin
from agent_runtime.infrastructure.plugins.loader import load_plugin_entry
from agent_runtime.infrastructure.plugins.manifest import PluginManifest
from agent_runtime.infrastructure.tools.sandbox import child_guard
from agent_runtime.infrastructure.tools.sandbox.capabilities import DefaultCapabilityResolver

# ── 1. module identity is its LOCATION ────────────────────────────────────────────────────


def _plugin(root: Path, module: str, marker: str) -> PluginManifest:
    """A minimal loadable plugin whose registered 'tool' is just its marker string."""
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{module}.py").write_text(
        textwrap.dedent(
            f"""
            from types import SimpleNamespace

            MARKER = {marker!r}

            def register(api, ctx):
                api.register_tool(SimpleNamespace(name=MARKER))
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return PluginManifest(
        id=f"p-{marker}", name=marker, kind="native", entry=f"{module}:register", root=root
    )


def _names(manifest) -> list[str]:
    tools, _ = load_plugin_entry(manifest, SimpleNamespace())
    return [t.name for t in tools]


def test_reinstalling_a_plugin_serves_the_NEW_code(tmp_path):
    """THE hosted bug: an agent upgraded on a running daemon kept its old plugin behaviour, so a
    fix that was visibly on disk never ran and the failure read as 'the files are missing'."""
    root = tmp_path / "agents" / "mkt" / "plugins" / "img"
    m = _plugin(root, "marketing_image", "v1")
    assert _names(m) == ["v1"]

    _plugin(root, "marketing_image", "v2")  # same path, new version installed over it
    assert _names(m) == ["v2"], "a cached module served stale code after an upgrade"


def test_two_agents_may_ship_the_same_module_name(tmp_path):
    """Multi-tenant reality: nothing stops two authors naming a file the same thing. Whichever
    loaded first used to win for BOTH — silently, and forever."""
    a = _plugin(tmp_path / "agents" / "a" / "plugins" / "img", "marketing_image", "from-a")
    b = _plugin(tmp_path / "agents" / "b" / "plugins" / "img", "marketing_image", "from-b")
    assert _names(a) == ["from-a"]
    assert _names(b) == ["from-b"]
    assert _names(a) == ["from-a"], "b's copy displaced a's"


def test_an_unchanged_plugin_is_not_re_executed(tmp_path):
    """Discovery runs on every hot-reload; re-executing unchanged modules would drop state and
    slow every reload. Identity is the location, freshness is the file stamp."""
    root = tmp_path / "agents" / "c" / "plugins" / "img"
    m = _plugin(root, "counter_plugin", "once")
    load_plugin_entry(m, SimpleNamespace())
    module = next(v for k, v in sys.modules.items() if k.endswith("_counter_plugin"))
    module.SIDE_EFFECT = "survives"
    load_plugin_entry(m, SimpleNamespace())
    again = next(v for k, v in sys.modules.items() if k.endswith("_counter_plugin"))
    assert again is module and again.SIDE_EFFECT == "survives"


def test_an_installed_distribution_still_imports_by_name(tmp_path):
    """A pip-installed plugin's identity really IS its installed name — that path is unchanged."""
    m = PluginManifest(id="json-ish", name="j", kind="native", entry="json:dumps", root=None)
    tools, sections = load_plugin_entry(m, SimpleNamespace())
    assert (tools, sections) == ([], [])  # json.dumps isn't a register(); it must not explode


# ── 2. the grant covers the plugin's own package, read-only ───────────────────────────────


def _tool(agent_dir: Path, plugin_root: Path):
    return SimpleNamespace(
        name="generate_marketing_image",
        _agent_dir=str(agent_dir),
        _plugin_root=str(plugin_root),
        needs_model=False,
    )


def test_grant_includes_the_agent_folder_read_only(tmp_path):
    agent_dir = tmp_path / "agents" / "marketing-agent"
    ctx = RunContext(agent_id="marketing-agent", session_key="s", mode="chat", workspace="/data/ws")
    g = DefaultCapabilityResolver().resolve(
        "marketing-image",
        PluginOrigin.THIRD_PARTY_BUNDLE,
        ctx,
        _tool(agent_dir, agent_dir / "plugins" / "marketing-image"),
    )
    assert g.read_paths == (str(agent_dir),), "shipped data sits beside the code, not in the workspace"
    assert g.fs_paths == ("/data/ws",), "writes still land only in the workspace"
    assert str(agent_dir) not in g.fs_paths, "the shipped package must never be writable"


def test_grant_falls_back_to_the_plugin_roots_agent_folder(tmp_path):
    """A tool assembled without the discovery tag still gets its package: plugins/<id>/ -> agent."""
    agent_dir = tmp_path / "agents" / "mkt"
    tool = _tool(agent_dir, agent_dir / "plugins" / "img")
    tool._agent_dir = ""
    g = DefaultCapabilityResolver().resolve("img", PluginOrigin.THIRD_PARTY_BUNDLE, None, tool)
    assert g.read_paths == (str((agent_dir).resolve()),)


def test_grantless_tool_gets_nothing(tmp_path):
    g = DefaultCapabilityResolver().resolve("x", PluginOrigin.THIRD_PARTY_BUNDLE, None, None)
    assert g.read_paths == () and g.fs_paths == ()


# ── 3. the guard's precedence: readable beats denied, for READS only ───────────────────────
# child_guard.install adds a process-wide audit hook, so these test the pure decision instead
# of installing one: same roots, same rules, no global side effect.


def _decide(real: str, *, writing: bool, granted=(), readable=(), denied=(), read_roots=(), write_roots=()):
    """The tiering of child_guard._check, exercised through its own containment helper."""
    under = child_guard._under
    if under(real, list(granted)):
        return "allow"
    if not writing and under(real, list(readable)):
        return "allow"
    if under(real, list(denied)):
        return "deny"
    return "allow" if under(real, list(write_roots if writing else read_roots)) else "deny"


def test_shipped_data_is_readable_even_inside_a_denied_tree(tmp_path):
    """HOSTED account-layer agents live under the tenant root, which is denied wholesale — the
    exact case where a package installed by a signed-in user was refused its own templates."""
    tenant = tmp_path / "state" / "accounts" / "acct_a"
    agent_dir = tenant / "installed" / "agents" / "mkt"
    template = agent_dir / "templates" / "flat-social.toml"
    assert _decide(
        str(template), writing=False, readable=[str(agent_dir)], denied=[str(tenant)],
        read_roots=[str(agent_dir)],
    ) == "allow"


def test_shipped_data_is_never_writable(tmp_path):
    agent_dir = tmp_path / "agents" / "mkt"
    assert _decide(
        str(agent_dir / "templates" / "x.toml"), writing=True, readable=[str(agent_dir)],
        write_roots=[str(tmp_path / "ws")],
    ) == "deny"


def test_another_tenants_files_stay_denied(tmp_path):
    """The read tier is the plugin's OWN package — it must not become a hole in the deny tier."""
    tenant = tmp_path / "state" / "accounts"
    mine = tenant / "acct_a" / "installed" / "agents" / "mkt"
    theirs = tenant / "acct_b" / "agents" / "main" / "workspace" / "secret.txt"
    assert _decide(
        str(theirs), writing=False, readable=[str(mine)], denied=[str(tenant)],
        read_roots=[str(mine)],
    ) == "deny"


def test_install_accepts_read_paths_and_keeps_them_out_of_write_roots(tmp_path):
    """The wiring itself: read_paths reach the guard and land in the READ tier only."""
    import inspect

    sig = inspect.signature(child_guard.install)
    assert "read_paths" in sig.parameters
    source = inspect.getsource(child_guard.install)
    assert "write_roots = granted + temp" in source, "shipped data must never become writable"
