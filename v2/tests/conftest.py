import asyncio
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Make the repo root AND every built-in plugin bundle importable. Tool implementations live in
# plugins/<bundle>/ now (migrated out of agentd core); at runtime the plugin loader puts each
# bundle dir on sys.path so its modules import by bare name (`from fs_tools import ReadTool`).
# Mirror that here so tests can import a migrated tool the same way — future-proof: a new bundle
# is picked up automatically. (Module names across bundles are kept unique to avoid shadowing.)
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_plugins = _ROOT / "plugins"
if _plugins.is_dir():
    for _sub in sorted(_plugins.iterdir()):
        if (_sub / "plugin.toml").is_file():
            sys.path.insert(0, str(_sub))

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
