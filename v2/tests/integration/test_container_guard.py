import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.config import load_config
from agent_runtime.infrastructure.tools.guard import GuardedTool
from agent_runtime.main.container import build_service


def test_container_wraps_every_tool():
    svc = build_service(load_config(), None, None)
    tools = svc._tools
    assert tools and all(isinstance(t, GuardedTool) for t in tools)
    # names preserved through the wrapper (the model/engine see no difference)
    names = [t.name for t in tools]
    assert "web_search" in names and "exec" in names and "read" in names
