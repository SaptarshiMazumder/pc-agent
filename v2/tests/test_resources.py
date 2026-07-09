"""Resource Manager: a cached, described index of workspace resources + CRUD on the fly.
Store (sqlite), describer (deterministic: text first-line + image dimensions), manager
(reconcile/create/edit/delete/manifest/describe_rich), and the `resource` tool."""

import asyncio
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from resource_tool import ResourceTool

from agentd.application import run_context as rc
from agentd.application.run_context import RunContext
from agentd.domain.resource import Resource
from agentd.infrastructure.resources.describe import BasicDescriber, _image_dims
from agentd.infrastructure.resources.manager import ResourceManager
from agentd.infrastructure.resources.store import SqliteResourceStore


def _png(w, h):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h)


# ---- store -----------------------------------------------------------------


def test_store_crud_and_agent_scope(tmp_path):
    s = SqliteResourceStore(tmp_path / "r.sqlite")
    s.put("scout", Resource("a.py", "script", 10, "sig1", "desc a"))
    s.put("main", Resource("a.py", "script", 10, "sig1", "other"))
    assert s.get("scout", "a.py").description == "desc a"
    assert [r.rel_path for r in s.list("scout")] == ["a.py"]
    assert s.delete("scout", "a.py") is True
    assert s.get("scout", "a.py") is None
    assert s.get("main", "a.py") is not None  # other agent untouched
    s.close()


# ---- describer -------------------------------------------------------------


def test_image_dims_png():
    assert _image_dims(_png(1200, 800)) == (1200, 800)


def test_describer_text_and_image(tmp_path):
    d = BasicDescriber()
    assert "makes the sheet" in d.describe("script", Path("g.py"), b"# makes the sheet\nx=1\n")
    assert d.describe("image", Path("c.png"), _png(640, 480)) == "PNG image, 640x480"
    # text data -> generic first line (the CSV header); no per-format label table
    assert d.describe("data", Path("x.csv"), b"name,amount\na,1\n") == "name,amount"
    # binary blob -> no description (filename + extension + size already say what it is)
    assert d.describe("data", Path("x.xlsx"), b"PK\x03\x04\x00\x00binary\x00stuff") == ""


# ---- manager ---------------------------------------------------------------


def _mgr(tmp_path):
    store = SqliteResourceStore(tmp_path / "r.sqlite")
    return ResourceManager(store, BasicDescriber(), max_files=100), store


def test_create_writes_indexes_describes(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    m, store = _mgr(tmp_path)
    r = m.create(ws, "scout", "gen.py", "# makes the sheet\nprint(1)\n")
    assert r.kind == "script" and "makes the sheet" in r.description
    assert (ws / "gen.py").read_text(encoding="utf-8").startswith("# makes the sheet")
    assert store.get("scout", "gen.py") is not None


def test_manifest_has_descriptions(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    m, _ = _mgr(tmp_path)
    m.create(ws, "scout", "gen.py", "# makes the sheet\n")
    man = m.manifest(ws, "scout")
    assert "## Your workspace resources" in man and "gen.py" in man and "makes the sheet" in man


def test_reconcile_skips_home_directory(tmp_path):
    # main's workspace defaults to home — never crawl/summarize it (slow + privacy)
    m, _ = _mgr(tmp_path)
    items, capped = m.reconcile(Path.home(), "ag")
    assert items == [] and capped is False


def test_reconcile_skips_tmp_scratch(tmp_path):
    # the tmp/ scratch dir is never indexed or enriched (throwaway files)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "keep.py").write_text("x", encoding="utf-8")
    (ws / "tmp").mkdir()
    (ws / "tmp" / "junk.txt").write_text("x", encoding="utf-8")
    m, _ = _mgr(tmp_path)
    items, _ = m.reconcile(ws, "ag")
    rels = [r.rel_path for r in items]
    assert "keep.py" in rels and not any("tmp/" in r for r in rels)


def test_reconcile_prunes_deleted_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    m, store = _mgr(tmp_path)
    m.create(ws, "scout", "a.py", "print(1)")
    (ws / "a.py").unlink()
    m.reconcile(ws, "scout")
    assert store.get("scout", "a.py") is None


def test_delete_removes_file_and_index(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    m, store = _mgr(tmp_path)
    m.create(ws, "scout", "a.py", "print(1)")
    assert m.delete(ws, "scout", "a.py") is True
    assert not (ws / "a.py").exists() and store.get("scout", "a.py") is None


def test_path_escape_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    m, _ = _mgr(tmp_path)
    with pytest.raises(ValueError):
        m.create(ws, "scout", "../evil.py", "x")


def test_cached_description_reused_when_unchanged(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    store = SqliteResourceStore(tmp_path / "r.sqlite")

    class Counting:
        n = 0

        def describe(self, kind, path, sample):
            self.__class__.n += 1
            return "d"

    m = ResourceManager(store, Counting(), max_files=100)
    m.create(ws, "a", "x.py", "print(1)")
    after = Counting.n
    m.reconcile(ws, "a")  # unchanged -> served from cache, no re-describe
    assert Counting.n == after


def test_enrich_runs_in_background_not_inline(tmp_path):
    # basic description is returned IMMEDIATELY (sync, cheap); the expensive rich describe
    # runs as an independent background task and caches later — never on the agent's path.
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "chart.png").write_bytes(_png(4, 2))
    store = SqliteResourceStore(tmp_path / "r.sqlite")
    calls = []

    async def rich(kind, path, sample):
        calls.append(path.name)
        return "a bar chart of monthly costs"

    m = ResourceManager(store, BasicDescriber(), rich_fn=rich)

    async def run():
        items, _ = m.reconcile(ws, "a")  # sync basic index + auto-enqueue enrich
        assert items[0].description.startswith("PNG image")  # cheap dims, available NOW
        assert store.get("a", "chart.png").description.startswith("PNG image")  # not yet enriched
        assert m._enrich_tasks  # the rich describe is running in the background
        await asyncio.gather(*m._enrich_tasks)  # (test only) let the background finish

    asyncio.run(run())

    assert calls == ["chart.png"]  # rich ran exactly once
    assert (
        store.get("a", "chart.png").description == "a bar chart of monthly costs"
    )  # enriched later


def test_enrich_deduped_per_file_version(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "chart.png").write_bytes(_png(4, 2))
    store = SqliteResourceStore(tmp_path / "r.sqlite")
    calls = []

    async def rich(kind, path, sample):
        calls.append(1)
        return "cap"

    m = ResourceManager(store, BasicDescriber(), rich_fn=rich)

    async def run():
        m.reconcile(ws, "a")
        await asyncio.gather(*m._enrich_tasks)
        m.reconcile(ws, "a")  # unchanged file -> NOT re-enriched
        await asyncio.gather(*m._enrich_tasks)

    asyncio.run(run())
    assert len(calls) == 1  # enriched once for this version


def test_enriched_flag_persists_across_restart(tmp_path):
    # enrich once -> the `enriched` flag is stored; a fresh manager on the SAME db
    # (a "restart", empty in-memory dedup) does NOT re-run the expensive describe.
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("# does X\nprint(1)\n", encoding="utf-8")
    db = tmp_path / "r.sqlite"
    calls = []

    async def rich(kind, path, sample):
        calls.append(path.name)
        return "summary of a.py"

    store1 = SqliteResourceStore(db)
    m1 = ResourceManager(store1, BasicDescriber(), rich_fn=rich)

    async def run1():
        m1.reconcile(ws, "ag")
        await asyncio.gather(*m1._enrich_tasks)

    asyncio.run(run1())
    assert calls == ["a.py"]  # summarized once
    assert store1.get("ag", "a.py").enriched is True  # flag persisted in the DB
    store1.close()

    # "restart": brand-new manager + store on the same DB, fresh in-memory state
    store2 = SqliteResourceStore(db)
    m2 = ResourceManager(store2, BasicDescriber(), rich_fn=rich)

    async def run2():
        m2.reconcile(ws, "ag")
        await asyncio.gather(*m2._enrich_tasks)

    asyncio.run(run2())
    assert calls == ["a.py"]  # NOT re-summarized after restart
    store2.close()


def test_enqueue_enrich_noop_without_rich_fn(tmp_path):
    # no rich_fn -> nothing expensive -> no background task at all (basic stays sync)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("print(1)", encoding="utf-8")
    m = ResourceManager(SqliteResourceStore(tmp_path / "r.sqlite"), BasicDescriber())

    async def run():
        m.reconcile(ws, "a")
        return m._enrich_tasks

    assert asyncio.run(run()) == set()


def test_describe_rich_uses_injected_fn(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "chart.png").write_bytes(_png(4, 2))
    store = SqliteResourceStore(tmp_path / "r.sqlite")

    async def rich(kind, path, sample):
        return "a bar chart of monthly costs"

    m = ResourceManager(store, BasicDescriber(), rich_fn=rich)
    desc = asyncio.run(m.describe_rich(ws, "a", "chart.png"))
    assert desc == "a bar chart of monthly costs"
    assert store.get("a", "chart.png").description == "a bar chart of monthly costs"


# ---- tool ------------------------------------------------------------------

# ---- vision rich_fn (gating + fallback; no live API) -----------------------


def test_build_rich_fn_off_by_default():
    from agentd.infrastructure.resources.vision import build_rich_fn

    assert build_rich_fn(SimpleNamespace(resource_vision_enabled=False)) is None


def test_build_rich_fn_none_without_api_key(monkeypatch):
    from agentd.infrastructure.resources.vision import build_rich_fn

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert build_rich_fn(SimpleNamespace(resource_vision_enabled=True)) is None


def test_rich_fn_skips_non_images_without_api_call(monkeypatch):
    # builds the fn (key present) but a non-image returns "" BEFORE any client/network use
    from agentd.infrastructure.resources.vision import build_rich_fn

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    fn = build_rich_fn(
        SimpleNamespace(
            resource_vision_enabled=True,
            resource_vision_model="m",
            resource_vision_timeout_seconds=5,
        )
    )
    assert fn is not None
    assert asyncio.run(fn("script", Path("x.py"), b"print(1)")) == ""


def test_build_rich_fn_on_for_summarize_only(monkeypatch):
    from agentd.infrastructure.resources.vision import build_rich_fn

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    fn = build_rich_fn(
        SimpleNamespace(
            resource_summarize_enabled=True,
            plugins={"resources": {"tools": {"summarize": {"model": "lm_studio/qwen"}}}},
            resource_vision_timeout_seconds=5,
        )
    )
    assert fn is not None  # summarize alone is enough to build it


def test_summary_tier_needs_no_gemini_key(monkeypatch):
    # text summaries go through litellm -> they do NOT need a Gemini key (the split)
    from agentd.infrastructure.resources.vision import build_rich_fn

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    fn = build_rich_fn(
        SimpleNamespace(
            resource_summarize_enabled=True,
            plugins={"resources": {"tools": {"summarize": {"model": "lm_studio/qwen"}}}},
            resource_vision_timeout_seconds=5,
        )
    )
    assert fn is not None  # summary tier works with no Gemini key


def test_vision_only_without_key_is_none(monkeypatch):
    # vision needs google-genai + a Gemini key; without it (and no summary tier) -> None
    from agentd.infrastructure.resources.vision import build_rich_fn

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert build_rich_fn(SimpleNamespace(resource_vision_enabled=True)) is None


def test_rich_fn_skips_text_when_summarize_off(monkeypatch):
    # vision ON, summarize OFF -> a script returns "" (no LLM call), stays on basic
    from agentd.infrastructure.resources.vision import build_rich_fn

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    fn = build_rich_fn(
        SimpleNamespace(
            resource_vision_enabled=True,
            resource_summarize_enabled=False,
            resource_vision_model="m",
            resource_vision_timeout_seconds=5,
        )
    )
    assert asyncio.run(fn("script", Path("a.py"), b"print(1)")) == ""


def test_text_for_helper():
    from agentd.infrastructure.resources.vision import _text_for

    assert "print" in _text_for(Path("a.py"), b"print(1)\n")  # text -> decoded
    assert _text_for(Path("a.bin"), b"\x00\x01\x02binary") == ""  # binary non-doc -> ""


def test_describe_rich_falls_back_to_basic_when_rich_empty(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("# a tool that does X\nprint(1)\n", encoding="utf-8")
    store = SqliteResourceStore(tmp_path / "r.sqlite")

    async def rich(kind, path, sample):
        return ""  # rich declines (e.g. non-image / failure)

    m = ResourceManager(store, BasicDescriber(), rich_fn=rich)
    desc = asyncio.run(m.describe_rich(ws, "a", "a.py"))
    assert "a tool that does X" in desc  # basic kept, never overwritten with ""


def test_resource_tool_crud_via_run_context(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    m = ResourceManager(SqliteResourceStore(tmp_path / "r.sqlite"), BasicDescriber())
    tool = ResourceTool(m)
    tok = rc._current.set(RunContext("scout", "s", "interactive", workspace=str(ws)))
    try:
        out = asyncio.run(
            tool.execute(
                "c", {"action": "create", "path": "a.py", "content": "# hi\n"}, asyncio.Event()
            )
        )
        assert "created a.py" in out.content[0].text
        listed = asyncio.run(tool.execute("c", {"action": "list"}, asyncio.Event()))
        assert "a.py" in listed.content[0].text
        gone = asyncio.run(tool.execute("c", {"action": "delete", "path": "a.py"}, asyncio.Event()))
        assert "deleted a.py" in gone.content[0].text
        assert not (ws / "a.py").exists()
    finally:
        rc._current.reset(tok)
