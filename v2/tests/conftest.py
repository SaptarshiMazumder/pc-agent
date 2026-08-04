import asyncio
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Make the repo root AND every plugin bundle importable. Tool implementations live in
# plugins/<bundle>/ now (migrated out of agentd core); at runtime the plugin loader puts each
# bundle dir on sys.path so its modules import by bare name (`from fs_tools import ReadTool`).
# Mirror that here so tests can import a migrated tool the same way — future-proof: a new bundle
# is picked up automatically. (Module names across bundles are kept unique to avoid shadowing.)
#
# BOTH TIERS, because both hold real logic: the shared bundles in plugins/, and the AGENT-PRIVATE
# ones in agents/<id>/plugins/<bundle>/ (e.g. agent-builder's own authoring tools). The runtime
# loader treats them identically — discover_agent_plugins does the same sys.path insert — so a
# test importing either should not have to care which tier a bundle lives in.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _add_bundles(parent: Path) -> None:
    if not parent.is_dir():
        return
    for _sub in sorted(parent.iterdir()):
        if (_sub / "plugin.toml").is_file():
            sys.path.insert(0, str(_sub))


_add_bundles(_ROOT / "plugins")
_agents = _ROOT / "agents"
if _agents.is_dir():
    for _agent in sorted(_agents.iterdir()):
        _add_bundles(_agent / "plugins")

# Tests are tiered by directory — tests/unit, tests/integration, tests/e2e — and each test
# is auto-stamped with its tier as a marker, so `pytest -m integration` and
# `pytest tests/integration` select the same set. New files inherit the tier from where
# they live; never add tier markers by hand.
_TESTS_DIR = Path(__file__).resolve().parent
_TIERS = {"unit", "integration", "e2e"}


def pytest_collection_modifyitems(config, items):
    for item in items:
        try:
            rel = Path(str(item.fspath)).resolve().relative_to(_TESTS_DIR)
        except ValueError:
            continue
        tier = rel.parts[0] if rel.parts else ""
        if tier in _TIERS:
            item.add_marker(getattr(pytest.mark, tier))
