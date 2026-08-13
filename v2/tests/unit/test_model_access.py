"""The ModelAccess funnel — ONE route to a model, correct in every deployment mode.

The property under test: a tool calls ``self.models.generate_image(...)`` (or the funnel's
``oneshot.generate_image``) and the RUNTIME decides the transport —

    local BYOK          -> direct google-genai on the user's own env key
    desktop cloud       -> the platform proxy's /gemini passthrough, paid by the CONNECTION's
                           own session token (who pays and who is metered cannot disagree)
    web hosted (forced) -> the same passthrough, paid by the server's master key
    Local pin           -> a connection that chose Local stays direct even where a proxy exists
    sandbox             -> the child's stub asks the host; the broker enforces the grant

— with the TOOL's code identical in all five. The fake here is ``google.genai.Client``, which is
exactly the boundary: everything above it (key choice, base_url, model normalization) is ours.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.distribution import parse_profile
from agent_runtime.infrastructure import accounts
from agent_runtime.infrastructure.llm import model_proxy, oneshot
from agent_runtime.infrastructure.llm.model_access import FunnelModelAccess, default_model_access

V2 = Path(__file__).resolve().parents[2]


def _config(proxy=None, platform_url=""):
    profile = parse_profile(
        {"platform": {"model_proxy_url": platform_url}} if platform_url else {},
        source_path="x" if platform_url else "",
    )
    return SimpleNamespace(model_proxy=proxy or {}, model_gateway={}, distribution=profile)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "AGENTD_MODEL_PROXY_URL",
        "AGENTD_MODEL_PROXY_KEY",
        "AGENTD_MODEL_GATEWAY_URL",
        "AGENTD_MODEL_GATEWAY_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    model_proxy.configure(_config())  # leave the module seam OFF for other tests


class _FakeClient:
    """Captures how the funnel constructed the SDK client and answers with one PNG part."""

    last: "_FakeClient | None" = None
    fail_first_with: str = ""  # set to simulate a 404 on the first generate_content

    def __init__(self, *, api_key=None, http_options=None):
        self.api_key = api_key
        self.http_options = http_options
        self.calls: list[dict] = []
        _FakeClient.last = self
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if _FakeClient.fail_first_with and len(self.calls) == 1:
            raise RuntimeError(_FakeClient.fail_first_with)
        blob = SimpleNamespace(data=b"PNGBYTES", mime_type="image/png")
        part = SimpleNamespace(inline_data=blob)
        cand = SimpleNamespace(content=SimpleNamespace(parts=[part]))
        return SimpleNamespace(candidates=[cand], text="")


@pytest.fixture
def fake_genai(monkeypatch):
    import google.genai as genai_mod

    _FakeClient.last = None
    _FakeClient.fail_first_with = ""
    monkeypatch.setattr(genai_mod, "Client", _FakeClient)
    return _FakeClient


# --------------------------------------------------------------------------- the mode matrix


def test_byok_goes_direct_on_the_users_own_key(fake_genai, monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "users-own-key")
    model_proxy.configure(_config())
    out = tmp_path / "art.png"
    r = oneshot.generate_image(model="gemini/gemini-3-pro-image", prompt="a cell", out_path=out)
    client = fake_genai.last
    assert client.api_key == "users-own-key"
    assert client.http_options is None, "direct means direct — no proxy base_url"
    assert client.calls[0]["model"] == "gemini-3-pro-image", "litellm-style id normalized bare"
    assert out.read_bytes() == b"PNGBYTES" and r["mime"] == "image/png"


def test_byok_with_no_key_fails_loud_and_names_the_fix(fake_genai, tmp_path):
    model_proxy.configure(_config())
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        oneshot.generate_image(model="m", prompt="p", out_path=tmp_path / "x.png")


def test_desktop_cloud_rides_the_passthrough_paid_by_the_connections_session(
    fake_genai, tmp_path
):
    """The desktop-cloud story: the flavor names the proxy, a signed-in CONNECTION pays with its
    own session token, and the native SDK is pointed at the proxy's /gemini passthrough."""
    model_proxy.configure(_config(platform_url="http://proxy.example:4000"))
    token = accounts.set_account({"account_id": "a1", "session_token": "sess_abc"})
    try:
        oneshot.generate_image(model="gemini-3-pro-image", prompt="p", out_path=tmp_path / "x.png")
    finally:
        accounts.reset_account(token)
    client = fake_genai.last
    assert client.api_key == "sess_abc", "the connection's own session pays"
    assert client.http_options.base_url == "http://proxy.example:4000/gemini"


def test_web_hosted_forced_pays_with_the_master_key(fake_genai, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTD_MODEL_PROXY_URL", "http://proxy:4000")
    monkeypatch.setenv("AGENTD_MODEL_PROXY_KEY", "sk-master")
    model_proxy.configure(_config())
    oneshot.generate_image(model="m", prompt="p", out_path=tmp_path / "x.png")
    client = fake_genai.last
    assert client.api_key == "sk-master"
    assert client.http_options.base_url == "http://proxy:4000/gemini"


def test_a_local_pinned_connection_stays_direct_even_where_a_proxy_exists(
    fake_genai, monkeypatch, tmp_path
):
    monkeypatch.setenv("GEMINI_API_KEY", "users-own-key")
    model_proxy.configure(_config(platform_url="http://proxy.example:4000"))
    acct = accounts.set_account({"account_id": "a1", "session_token": "sess_abc"})
    billing = model_proxy.set_billing("local")
    try:
        oneshot.generate_image(model="m", prompt="p", out_path=tmp_path / "x.png")
    finally:
        model_proxy.reset_billing(billing)
        accounts.reset_account(acct)
    client = fake_genai.last
    assert client.api_key == "users-own-key" and client.http_options is None


def test_proxied_turns_ignore_a_per_call_byok_key(fake_genai, monkeypatch, tmp_path):
    """`api_key` is a BYOK override only. Honouring it under the proxy would let a call be made
    that nobody meters — the same reason the sandbox stub ignores it."""
    monkeypatch.setenv("AGENTD_MODEL_PROXY_URL", "http://proxy:4000")
    monkeypatch.setenv("AGENTD_MODEL_PROXY_KEY", "sk-master")
    model_proxy.configure(_config())
    oneshot.generate_image(model="m", prompt="p", out_path=tmp_path / "x.png", api_key="smuggled")
    assert fake_genai.last.api_key == "sk-master"


def test_a_404_retries_once_on_the_preview_variant(fake_genai, monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    model_proxy.configure(_config())
    _FakeClient.fail_first_with = "404 NOT_FOUND: model not found"
    r = oneshot.generate_image(model="gemini-3-pro-image", prompt="p", out_path=tmp_path / "x.png")
    client = fake_genai.last
    assert [c["model"] for c in client.calls] == ["gemini-3-pro-image", "gemini-3-pro-image-preview"]
    assert r["model"] == "gemini-3-pro-image-preview"


def test_passthrough_is_off_exactly_when_apply_is():
    model_proxy.configure(_config())
    assert model_proxy.passthrough("gemini") == ("", "")


# --------------------------------------------------------------------------- the Tool port


def test_every_tool_holds_the_port_and_tests_can_fake_it():
    from agent_runtime.application.interfaces.model_access import ModelAccess
    from agent_runtime.application.interfaces.tool import Tool, ToolResult

    class T(Tool):
        name = "t"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, abort, on_update=None):
            return ToolResult.text("ok")

    tool = T()
    assert isinstance(tool.models, FunnelModelAccess)
    assert isinstance(tool.models, ModelAccess), "the default satisfies the Protocol"
    fake = object()
    tool._model_access = fake
    assert tool.models is fake, "injection wins — the test seam"


def test_the_default_port_is_one_shared_stateless_instance():
    assert default_model_access() is default_model_access()


# ------------------------------------------------------------------- retrofit proof: the tools


class _FakePort:
    """A ModelAccess fake that records the call and writes the file, like the real funnel."""

    def __init__(self):
        self.calls: list[dict] = []

    def generate_image(self, **kw):
        self.calls.append(kw)
        Path(kw["out_path"]).write_bytes(b"FAKE")
        return {"path": str(kw["out_path"]), "mime": "image/png", "model": kw["model"]}

    def vision(self, **kw):
        self.calls.append(kw)
        return "{}"

    def text(self, **kw):
        self.calls.append(kw)
        return ""


def _tool_config():
    return SimpleNamespace(plugins={}, model_defaults={}, workspace=".", config_path="x")


def test_edit_artwork_generates_through_the_port_with_no_env_key(tmp_path, monkeypatch):
    """The regression that broke hosted: the tool used to REQUIRE a raw env key before calling.
    Now it holds no key at all — the port decides transport, so no key means the port's problem
    (and in cloud mode, no problem)."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    sys.path.insert(0, str(V2 / "plugins" / "figure-art"))
    try:
        from edit_artwork_tool import EditArtworkTool
    finally:
        sys.path.pop(0)

    img = tmp_path / "fig.png"
    img.write_bytes(b"P")
    tool = EditArtworkTool(_tool_config())
    fake = _FakePort()
    tool._model_access = fake
    result = tool._run({"image": str(img), "instruction": "make the nozzle longer"})
    call = fake.calls[0]
    assert call["model"] == "gemini/gemini-3-pro-image", "house image-gen default via model_kind"
    assert call["reference_images"] == [img]
    assert result["out"].endswith("_edit.png")


def test_read_labels_strip_goes_through_the_port(tmp_path):
    sys.path.insert(0, str(V2 / "plugins" / "vision"))
    try:
        from read_labels_tool import ReadLabelsTool
    finally:
        sys.path.pop(0)

    labelled = tmp_path / "fig_labelled.png"
    labelled.write_bytes(b"P")
    tool = ReadLabelsTool(_tool_config())
    fake = _FakePort()
    tool._model_access = fake
    out = tool._strip_labels(labelled, None)
    call = fake.calls[0]
    assert "REMOVE every text label" in call["prompt"]
    assert call["reference_images"] == [labelled]
    assert out.name == "fig_labelled_textless.png" and out.read_bytes() == b"FAKE"


def test_no_plugin_module_imports_the_deleted_direct_sdk_helpers():
    """figure_art_gemini / vectorize_gemini are GONE — a lingering import is a boot failure."""
    hits = [
        p
        for p in (V2 / "plugins").rglob("*.py")
        if "figure_art_gemini" in p.read_text(encoding="utf-8", errors="ignore")
        or "vectorize_gemini" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits == []


# ------------------------------------------------------------------- sandbox: stub + broker


def test_the_sandbox_stub_serves_generate_image_and_intercepts_the_import():
    from agent_runtime.infrastructure.tools.sandbox import child_models

    sent: list[dict] = []

    class _Bridge:
        def request(self, kind, payload):
            sent.append({"kind": kind, **payload})
            return json.dumps({"path": payload["out_path"], "mime": "image/png", "model": "m"})

    saved = sys.modules.get(child_models._MODULE)
    try:
        child_models.install(_Bridge())
        # the exact import a plugin writes resolves to the stub, not the real funnel
        from agent_runtime.infrastructure.llm.oneshot import generate_image as gi

        assert isinstance(sys.modules[child_models._MODULE], types.ModuleType)
        r = gi(
            model="m",
            prompt="p",
            out_path="w/x.png",
            reference_images=["w/ref.png"],
            api_key="ignored-on-purpose",
        )
    finally:
        if saved is not None:
            sys.modules[child_models._MODULE] = saved
    assert r == {"path": "w/x.png", "mime": "image/png", "model": "m"}
    assert sent[0]["kind"] == "image" and sent[0]["reference_images"] == ["w/ref.png"]
    assert "api_key" not in sent[0], "a sandboxed credential must never travel"


def _broker(config=None, **grant_over):
    from agent_runtime.domain.sandbox import CapabilityGrant
    from agent_runtime.infrastructure.tools.sandbox.model_broker import SandboxModelBroker

    grant = CapabilityGrant(**grant_over)
    cfg = config or SimpleNamespace(sandbox_model_limits={})
    return SandboxModelBroker(cfg, plugin_id="plug", tool_name="tool", grant=grant)


def _serve(broker, request):
    import asyncio

    return asyncio.run(broker.serve(request))


def test_broker_serves_an_image_request_inside_the_grant(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ref = ws / "ref.png"
    ref.write_bytes(b"P")

    def fake_generate(**kw):
        Path(kw["out_path"]).write_bytes(b"OUT")
        return {"path": str(kw["out_path"]), "mime": "image/png", "model": kw["model"]}

    monkeypatch.setattr(oneshot, "generate_image", fake_generate)
    broker = _broker(fs_paths=(str(ws),), models=("gemini/gemini-3-pro-image",))
    reply = _serve(
        broker,
        {
            "t": "model_request",
            "id": "m1",
            "kind": "image",
            "model": "gemini/gemini-3-pro-image",
            "prompt": "p",
            "out_path": str(ws / "out.png"),
            "reference_images": [str(ref)],
        },
    )
    assert reply["ok"], reply
    assert json.loads(reply["text"])["mime"] == "image/png"
    assert (ws / "out.png").read_bytes() == b"OUT"


def test_broker_refuses_an_out_path_outside_the_grant(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    broker = _broker(fs_paths=(str(ws),), models=("m",))
    reply = _serve(
        broker,
        {
            "id": "m1",
            "kind": "image",
            "model": "m",
            "prompt": "p",
            "out_path": str(tmp_path / "elsewhere.png"),
        },
    )
    assert not reply["ok"] and "granted paths" in reply["error"]


def test_broker_refuses_an_image_request_with_no_out_path(tmp_path):
    broker = _broker(fs_paths=(str(tmp_path),), models=("m",))
    reply = _serve(broker, {"id": "m1", "kind": "image", "model": "m", "prompt": "p"})
    assert not reply["ok"] and "out_path" in reply["error"]


def test_broker_still_refuses_ungranted_models_for_images(tmp_path):
    broker = _broker(fs_paths=(str(tmp_path),), models=())
    reply = _serve(
        broker,
        {"id": "m1", "kind": "image", "model": "m", "prompt": "p", "out_path": str(tmp_path / "o.png")},
    )
    assert not reply["ok"] and "not granted model access" in reply["error"]
