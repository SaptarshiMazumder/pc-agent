"""A skill is invoked by READING its SKILL.md — surface that on the server log (INFO) AND to
clients/watch (via on_update)."""

import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.tools.fs_tools import ReadTool


def _read(path, on_update=None, **cfg):
    tool = ReadTool(SimpleNamespace(workspace=Path(path).parent, **cfg))
    return asyncio.run(tool.execute("c", {"path": str(path)}, asyncio.Event(), on_update=on_update))


def test_reading_skill_md_logs_and_emits(tmp_path, caplog):
    sk = tmp_path / "skills" / "web-access" / "SKILL.md"
    sk.parent.mkdir(parents=True)
    sk.write_text("# Web access playbook", encoding="utf-8")
    updates = []
    with caplog.at_level(logging.INFO, logger="agentd"):
        res = _read(sk, on_update=lambda r: updates.append(r))
    assert "Web access playbook" in res.content[0].text                      # the read still works
    assert any("skill invoked: web-access" in r.getMessage() for r in caplog.records)   # server log
    assert updates and "skill: web-access" in updates[0].content[0].text     # client/watch signal


def test_reading_a_normal_file_does_not_log_a_skill(tmp_path, caplog):
    f = tmp_path / "notes.md"
    f.write_text("just notes", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="agentd"):
        _read(f)
    assert not any("skill invoked" in r.getMessage() for r in caplog.records)
