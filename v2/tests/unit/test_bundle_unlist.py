"""`bundle unlist` — taking a bundle OFF the marketplace without touching anyone else's.

Exercised against a DIRECTORY registry, which shares every code path with s3 except transport.
The properties pinned:

  * plan-by-default: without --yes it is a pure read
  * only the named rows leave; the roster, the engine block and other creators' rows are carried
  * artifacts are KEPT unless --purge-artifacts (unlist is reversible, purge is not)
"""

import json
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.cli.commands.bundle import run_unlist


def registry(tmp_path: Path) -> Path:
    root = tmp_path / "registry"
    root.mkdir()
    index = {
        "schema": 2,
        "publisher_key": "ROOT",
        "publishers": {"roster": [{"id": "c-bob", "key": "K"}], "revoked": [], "issued": "t", "sig": "s"},
        "engine": {"win": {"url": "agentd-setup.exe"}},
        "bundles": [
            {
                "id": "expense-summarizer",
                "version": "1.0.1",
                "url": "expense-summarizer-1.0.1.agentpkg",
                "installers": [{"platform": "win", "url": "expense-summarizer-1.0.1-setup.exe"}],
            },
            {"id": "weather", "version": "2.0.0", "url": "weather-2.0.0.agentpkg"},
        ],
    }
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    (root / "expense-summarizer-1.0.1.agentpkg").write_bytes(b"PKG")
    (root / "expense-summarizer-1.0.1-setup.exe").write_bytes(b"EXE")
    (root / "weather-2.0.0.agentpkg").write_bytes(b"PKG2")
    return root


def unlist(root: Path, *ids, yes=False, purge=False) -> int:
    return run_unlist(
        Namespace(ids=list(ids), to=str(root), purge_artifacts=purge, yes=yes)
    )


def read_index(root: Path) -> dict:
    return json.loads((root / "index.json").read_text(encoding="utf-8"))


def test_without_yes_it_is_a_plan_and_nothing_changes(tmp_path):
    root = registry(tmp_path)
    before = read_index(root)

    assert unlist(root, "expense-summarizer") == 0

    assert read_index(root) == before
    assert (root / "expense-summarizer-1.0.1.agentpkg").exists()


def test_unlist_removes_only_the_named_rows_and_carries_everything_else(tmp_path):
    root = registry(tmp_path)

    assert unlist(root, "expense-summarizer", yes=True) == 0

    index = read_index(root)
    assert [b["id"] for b in index["bundles"]] == ["weather"]
    # nothing else moved: trust and the engine survive an unlist byte-for-byte
    assert index["publishers"]["roster"] == [{"id": "c-bob", "key": "K"}]
    assert index["engine"] == {"win": {"url": "agentd-setup.exe"}}
    # reversible by default: the files are still there
    assert (root / "expense-summarizer-1.0.1.agentpkg").exists()
    assert (root / "expense-summarizer-1.0.1-setup.exe").exists()


def test_purge_artifacts_deletes_the_files_the_rows_pointed_at(tmp_path):
    root = registry(tmp_path)

    assert unlist(root, "expense-summarizer", yes=True, purge=True) == 0

    assert not (root / "expense-summarizer-1.0.1.agentpkg").exists()
    assert not (root / "expense-summarizer-1.0.1-setup.exe").exists()
    assert (root / "weather-2.0.0.agentpkg").exists()  # nobody else's files


def test_an_unknown_id_is_refused_naming_what_the_registry_has(tmp_path, capsys):
    root = registry(tmp_path)
    assert unlist(root, "nope", yes=True) == 1
    out = capsys.readouterr().out
    assert "not in this registry: nope" in out
    assert "expense-summarizer" in out
    assert read_index(root)["bundles"], "a refusal must change nothing"


def test_the_publish_service_url_is_rejected_as_a_target(tmp_path, capsys):
    assert (
        run_unlist(
            Namespace(ids=["x"], to="http://example.com:4300", purge_artifacts=False, yes=True)
        )
        == 1
    )
    assert "OPERATOR command" in capsys.readouterr().out
