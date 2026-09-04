"""A sandboxed plugin may call an API — through the host, never through a socket of its own.

No network was the right default and the wrong end state: "wrap an API" is most of what people
build, and a plugin that calls one worked for its author and was dead for everyone who installed
the agent. Network is now INVERTED like model calls: the plugin asks, the host performs.

Two properties carry the design, and every test here defends one of them:

  * the NETWORK IS OPEN but the socket stays host-side (2026-09): a plugin fetches any host with
    no declaration, yet still cannot dial for itself — only the operator's own deny/allow knobs
    (a deployment fencing off its metadata endpoints) refuse a host;
  * the CREDENTIAL never crosses. A plugin writes `${NAME}` and the host substitutes at send
    time, so plugin code cannot read the key or keep it — and it can only NAME secrets its
    manifest declared.

The adversarial cases spawn a REAL child that really tries the escape, on the same principle as
the rest of the subprocess-sandbox suite: a mocked boundary proves nothing about the boundary.
"""

import asyncio
import json
import sys
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.domain.sandbox import CapabilityGrant, PluginOrigin
from agent_runtime.domain.sandbox_net import (
    resolve_allowlist,
    substitute,
    undeclared_placeholders,
)
from agent_runtime.infrastructure.plugins.manifest import load_manifest
from agent_runtime.infrastructure.tools.sandbox.capabilities import DefaultCapabilityResolver
from agent_runtime.infrastructure.tools.sandbox.fetch_broker import SandboxFetchBroker


class _Cfg:
    def __init__(self, **kw):
        self.sandbox_net_allow = kw.pop("net_allow", ())
        self.sandbox_net_deny = kw.pop("net_deny", ())
        self.sandbox_fetch_limits = kw.pop("limits", {})
        self.plugins = kw.pop("plugins", {})
        for k, v in kw.items():
            setattr(self, k, v)


# --- the allowlist rule -----------------------------------------------------


def test_a_subdomain_wildcard_does_not_match_a_lookalike_host():
    """`*.acme.com` must not admit `acme.com.evil.net` — the mistake a plain suffix test makes,
    and the whole reason this is a function and not an `endswith`."""
    allowed = resolve_allowlist(["*.acme.com"], (), ())
    from agent_runtime.domain.sandbox_net import matches_any

    assert matches_any("api.acme.com", allowed)
    assert not matches_any("acme.com.evil.net", allowed)
    assert not matches_any("acme.com", allowed)  # the wildcard is subdomains ONLY


def test_the_operator_can_narrow_but_never_widen():
    """A declaration is what the user READ before installing. An operator allowlist that ADDED
    hosts would grant reach the package never disclosed."""
    assert resolve_allowlist(["api.acme.com"], ("other.com",), ()) == ()
    assert resolve_allowlist(["api.acme.com"], ("api.acme.com",), ()) == ("api.acme.com",)
    # nothing the operator writes can introduce a host the plugin did not declare
    assert resolve_allowlist([], ("anything.com",), ()) == ()


def test_a_bare_star_in_deny_switches_outbound_off():
    assert resolve_allowlist(["api.acme.com", "b.com"], (), ("*",)) == ()


def test_a_plugin_may_not_declare_everything():
    assert resolve_allowlist(["*"], (), ()) == ()


# --- the credential rule ----------------------------------------------------


def test_an_undeclared_secret_is_refused():
    stray = undeclared_placeholders(["Bearer ${OPENAI_API_KEY}"], ["ACME_API_KEY"])
    assert stray == {"OPENAI_API_KEY"}


def test_a_declared_secret_is_accepted():
    assert undeclared_placeholders(["Bearer ${ACME_API_KEY}"], ["ACME_API_KEY"]) == set()


def test_an_unresolvable_name_stays_literal():
    """Left visible rather than blanked: a header that silently lost its credential is a
    confusing 401, one carrying `${NAME}` says what went wrong in the provider's own error."""
    assert substitute("Bearer ${NOPE}", {}) == "Bearer ${NOPE}"


# --- the manifest declaration -----------------------------------------------


def test_the_sandbox_table_is_parsed(tmp_path):
    (tmp_path / "plugin.toml").write_text(
        'id = "acme"\nname = "Acme"\nkind = "native"\nentry = "acme:register"\n'
        '[sandbox]\nnet = ["API.Acme.com"]\nsecrets = ["ACME_API_KEY"]\n',
        encoding="utf-8",
    )
    m = load_manifest(tmp_path / "plugin.toml")
    assert m.sandbox == {"net": ["api.acme.com"], "secrets": ["ACME_API_KEY"]}


def test_it_is_separate_from_the_requires_gate(tmp_path):
    """`[requires]` means 'skip me unless present'; `[sandbox]` means 'leave these open'. One
    table answering both would make 'declared but missing' mean two incompatible things."""
    (tmp_path / "plugin.toml").write_text(
        'id = "acme"\nname = "Acme"\nkind = "native"\nentry = "acme:register"\n'
        '[requires]\nenv = ["MUST_BE_SET"]\n[sandbox]\nsecrets = ["ACME_API_KEY"]\n',
        encoding="utf-8",
    )
    m = load_manifest(tmp_path / "plugin.toml")
    assert m.requires == {"env": ["MUST_BE_SET"]}
    assert m.sandbox == {"secrets": ["ACME_API_KEY"]}


def test_a_plugin_that_declares_nothing_gets_nothing(tmp_path):
    (tmp_path / "plugin.toml").write_text(
        'id = "quiet"\nname = "Quiet"\nkind = "native"\nentry = "quiet:register"\n', encoding="utf-8"
    )
    assert load_manifest(tmp_path / "plugin.toml").sandbox == {}


# --- the grant --------------------------------------------------------------


class _Tool:
    name = "call_acme"
    needs_model = False

    def __init__(self, net=(), secrets=()):
        self._sandbox_net = net
        self._sandbox_secrets = secrets


def test_the_grant_carries_the_declared_hosts():
    grant = DefaultCapabilityResolver(config=_Cfg()).resolve(
        "acme", PluginOrigin.THIRD_PARTY_BUNDLE, None, _Tool(net=("api.acme.com",))
    )
    assert grant.net_allowlist == ("api.acme.com",)


def test_the_grant_still_carries_no_secrets():
    """THE invariant. grant.secrets becomes the child's ENVIRONMENT — a declared credential must
    never land there, which is the entire reason it is declared by name."""
    grant = DefaultCapabilityResolver(config=_Cfg()).resolve(
        "acme", PluginOrigin.THIRD_PARTY_BUNDLE, None,
        _Tool(net=("api.acme.com",), secrets=("ACME_API_KEY",)),
    )
    assert grant.secrets == {}


def test_a_plugin_that_declared_no_hosts_gets_no_network():
    grant = DefaultCapabilityResolver(config=_Cfg()).resolve(
        "quiet", PluginOrigin.THIRD_PARTY_BUNDLE, None, _Tool()
    )
    assert grant.net_allowlist == ()


# --- the broker -------------------------------------------------------------


def _broker(**kw):
    grant = CapabilityGrant(net_allowlist=kw.pop("allow", ("api.acme.com",)))
    return SandboxFetchBroker(
        _Cfg(**{k: v for k, v in kw.items() if k in ("plugins", "limits")}),
        plugin_id="acme",
        tool_name="call_acme",
        grant=grant,
        declared_secrets=kw.get("declared", ()),
    )


def _serve(broker, **request):
    return asyncio.run(broker.serve({"t": "fetch_request", "id": "f1", **request}))


def test_the_network_is_open_no_declaration_needed(http_server):
    """THE 2026-09 CONTRACT: a plugin with an EMPTY allowlist fetches any host. The per-plugin
    reach gate is gone — `[sandbox] net` now only names which ${SETTING}s are legal in a URL.
    Malice is handled at distribution (share-only today, marketplace review later), not here."""
    res = _serve(_broker(allow=()), url=f"http://{http_server}/v1/anything")
    assert not res["error"], res["error"]
    assert res["status"] == 200


def test_the_operator_deny_still_binds(http_server):
    """The deployment's own fence outlives the open network: a hosted daemon can still wall off
    its metadata endpoints and internal ports. Default empty, so a desktop never sees this."""
    host = http_server.split(":")[0]
    broker = SandboxFetchBroker(
        _Cfg(net_deny=(host,)),
        plugin_id="acme",
        tool_name="call_acme",
        grant=CapabilityGrant(net_allowlist=()),
    )
    res = _serve(broker, url=f"http://{http_server}/v1/x")
    assert res["error"] and "operator" in res["error"]


def test_file_urls_are_refused():
    """Otherwise the broker becomes a file-read oracle: a plugin that cannot open a path could
    ask the host to fetch it and hand back the contents."""
    res = _serve(_broker(allow=("api.acme.com",)), url="file:///etc/passwd")
    assert "scheme" in res["error"]


def test_asking_for_an_undeclared_secret_is_refused():
    res = _serve(
        _broker(declared=("ACME_API_KEY",)),
        url="https://api.acme.com/x",
        headers={"Authorization": "Bearer ${OPENAI_API_KEY}"},
    )
    assert "OPENAI_API_KEY" in res["error"]


def test_the_call_cap_is_enforced():
    b = _broker(limits={"max_calls": 0})
    # max_calls 0 disables the cap; use 1 and burn it on a refused-host call that never counts
    b = _broker(limits={"max_calls": 1})
    b._calls = 1
    res = _serve(b, url="https://api.acme.com/x")
    assert "limit reached" in res["error"]


# --- end to end, through a REAL child process and a REAL server -------------


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's naming
        body = json.dumps({"auth": self.headers.get("Authorization", ""), "path": self.path})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self):  # noqa: N802 — records a multipart upload so a test can prove it landed
        length = int(self.headers.get("Content-Length") or 0)
        _Handler.last_post = {
            "content_type": self.headers.get("Content-Type", ""),
            "body": self.rfile.read(length),
        }
        body = json.dumps({"name": "stored.png"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # keep pytest output clean
        pass


@pytest.fixture()
def http_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_the_host_substitutes_the_credential_the_child_never_sees(http_server, monkeypatch):
    """The end-to-end property, stated as a single assertion: the request ARRIVES with the real
    key, and the only place that value ever existed is this process."""
    monkeypatch.setenv("ACME_API_KEY", "sk-secret-value")
    host = http_server.split(":")[0]
    broker = SandboxFetchBroker(
        _Cfg(),
        plugin_id="acme",
        tool_name="call_acme",
        grant=CapabilityGrant(net_allowlist=(host,)),
        declared_secrets=("ACME_API_KEY",),
    )
    res = _serve(
        broker,
        url=f"http://{http_server}/v1/things",
        headers={"Authorization": "Bearer ${ACME_API_KEY}"},
    )
    assert not res["error"], res["error"]
    assert res["status"] == 200
    assert json.loads(res["text"])["auth"] == "Bearer sk-secret-value"


def test_the_secret_value_is_not_in_anything_sent_to_the_child(http_server, monkeypatch):
    """The response frame is the ONLY thing that goes back down the pipe. If the key appeared in
    it — echoed by a server, or carelessly attached — the plugin would have it after all."""
    monkeypatch.setenv("ACME_API_KEY", "sk-secret-value")
    host = http_server.split(":")[0]
    broker = SandboxFetchBroker(
        _Cfg(),
        plugin_id="acme",
        tool_name="call_acme",
        grant=CapabilityGrant(net_allowlist=(host,)),
        declared_secrets=("ACME_API_KEY",),
    )
    res = _serve(broker, url=f"http://{http_server}/v1/plain")
    assert "sk-secret-value" not in json.dumps(res)


def test_a_key_in_config_answers_a_declared_name(http_server, monkeypatch):
    """BYOK inside a shipped agent: the user pastes their key on the agent's settings page, which
    writes config.plugins.<id>.secrets.<NAME>. The plugin still never sees it."""
    monkeypatch.delenv("ACME_API_KEY", raising=False)
    host = http_server.split(":")[0]
    broker = SandboxFetchBroker(
        _Cfg(plugins={"acme": {"secrets": {"ACME_API_KEY": "from-settings"}}}),
        plugin_id="acme",
        tool_name="call_acme",
        grant=CapabilityGrant(net_allowlist=(host,)),
        declared_secrets=("ACME_API_KEY",),
    )
    res = _serve(
        broker,
        url=f"http://{http_server}/v1/things",
        headers={"Authorization": "Bearer ${ACME_API_KEY}"},
    )
    assert json.loads(res["text"])["auth"] == "Bearer from-settings"


# --- a file riding along: multipart upload through the broker ---------------
# What images need — a LoadImage node eats what is in the SERVER's input folder, and the only
# way to get a chat attachment there is a multipart POST. Bytes cannot ride the JSON pipe, so
# the child names a PATH and the host reads and sends it. The path check is the fs sandbox
# doing its job at this boundary: the run's own files, not the machine's.


def test_a_workspace_file_uploads_as_multipart(http_server, tmp_path):
    img = tmp_path / "uploads" / "face.png"
    img.parent.mkdir()
    img.write_bytes(b"\x89PNG-not-really-but-bytes")
    broker = SandboxFetchBroker(
        _Cfg(),
        plugin_id="acme",
        tool_name="upload",
        grant=CapabilityGrant(net_allowlist=(), fs_paths=(str(tmp_path),)),
    )
    res = _serve(
        broker,
        url=f"http://{http_server}/upload/image",
        method="POST",
        file_path="uploads/face.png",  # workspace-RELATIVE, like every file tool
        file_field="image",
        form_fields={"subfolder": "chat"},
    )
    assert not res["error"], res["error"]
    assert res["status"] == 200
    posted = _Handler.last_post
    assert posted["content_type"].startswith("multipart/form-data")
    assert b"\x89PNG-not-really-but-bytes" in posted["body"]  # the bytes ARRIVED
    assert b'name="image"' in posted["body"] and b"face.png" in posted["body"]
    assert b'name="subfolder"' in posted["body"] and b"chat" in posted["body"]


def test_a_file_outside_the_run_s_scope_is_refused(http_server, tmp_path):
    """The broker must not become a disk-read oracle: an open network plus an unchecked path
    would let a plugin post any file on the machine to a server of its choice. The fs grant is
    the boundary that still stands."""
    secret = tmp_path / "elsewhere" / "id_rsa"
    secret.parent.mkdir()
    secret.write_text("PRIVATE", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    broker = SandboxFetchBroker(
        _Cfg(),
        plugin_id="acme",
        tool_name="upload",
        grant=CapabilityGrant(net_allowlist=(), fs_paths=(str(ws),)),
    )
    res = _serve(
        broker,
        url=f"http://{http_server}/upload/image",
        method="POST",
        file_path=str(secret),
    )
    assert res["status"] == 0
    assert "outside this run's files" in res["error"]


# --- a file coming BACK: save_path downloads through the broker -------------
# The other direction of the multipart lane: a rendered image is not text, `Response.text`
# would mangle it, so the host streams the bytes to a workspace file and the child gets the
# path. The write check mirrors the read check — and is stricter: fs_paths only, because a
# download that could land in `read_paths` would let a remote server rewrite the agent's own
# shipped code.

PNG_BYTES = b"\x89PNG\r\n\x1a\n-fake-but-binary-\x00\xff\xfe"


class _MediaHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(PNG_BYTES)))
        self.end_headers()
        self.wfile.write(PNG_BYTES)

    def log_message(self, *a):
        pass


@pytest.fixture()
def media_server():
    srv = HTTPServer(("127.0.0.1", 0), _MediaHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_a_download_lands_in_the_workspace_bytes_intact(media_server, tmp_path):
    broker = SandboxFetchBroker(
        _Cfg(),
        plugin_id="acme",
        tool_name="download",
        grant=CapabilityGrant(net_allowlist=(), fs_paths=(str(tmp_path),)),
    )
    res = _serve(
        broker,
        url=f"http://{media_server}/view",
        save_path="outputs/render.png",  # workspace-relative, like every file tool
    )
    assert not res["error"], res["error"]
    assert res["status"] == 200
    saved = tmp_path / "outputs" / "render.png"
    assert saved.read_bytes() == PNG_BYTES  # binary IDENTICAL — a text round-trip corrupts this


def test_a_download_may_not_land_outside_the_writable_scope(media_server, tmp_path):
    """And not in read_paths either: shipped files are what the author published, and a server
    that could overwrite them would be publishing code into the agent."""
    ws = tmp_path / "ws"
    ws.mkdir()
    shipped = tmp_path / "agent-def"
    shipped.mkdir()
    broker = SandboxFetchBroker(
        _Cfg(),
        plugin_id="acme",
        tool_name="download",
        grant=CapabilityGrant(
            net_allowlist=(), fs_paths=(str(ws),), read_paths=(str(shipped),)
        ),
    )
    res = _serve(
        broker,
        url=f"http://{media_server}/view",
        save_path=str(shipped / "plugin.py"),
    )
    assert res["status"] == 0
    assert "outside this run's writable space" in res["error"]
    assert not (shipped / "plugin.py").exists()


# --- the whole round trip, through a REAL child process ---------------------
# Everything above tests one side. This spawns a child that really imports `fetch`, really has no
# socket, and really gets an answer — the only version of this that proves the pipe works.

import textwrap  # noqa: E402

from agent_runtime.application.run_context import RunContext  # noqa: E402
from agent_runtime.infrastructure.tools.sandbox.subprocess_backend import (  # noqa: E402
    SubprocessPluginSandbox,
)

_PLUGIN = '''
from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.infrastructure.net.outbound import fetch


class ProbeTool(Tool):
    name = "probe"
    description = "calls an api"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        res = fetch(params["url"], headers={"Authorization": "Bearer ${ACME_API_KEY}"})
        return ToolResult.text(f"status={res.status} ok={res.ok} body={res.text} err={res.error}")


def register(api, ctx):
    api.register_tool(ProbeTool())
'''


class _NetTool:
    name = "probe"

    def __init__(self, entry, root, secrets=()):
        self._plugin_entry = entry
        self._plugin_root = root
        self._sandbox_secrets = tuple(secrets)


def _spawn(tmp_path, secrets=()):
    root = tmp_path / "plug"
    root.mkdir(exist_ok=True)
    (root / "net_probe.py").write_text(textwrap.dedent(_PLUGIN), encoding="utf-8")
    sandbox = SubprocessPluginSandbox(_Cfg())
    sandbox.register("acme", "probe", _NetTool("net_probe:register", str(root), secrets))
    return sandbox


def _child_run(sandbox, workspace, allowlist, url):
    return asyncio.run(
        sandbox.run_tool(
            "acme", "probe", "c1", {"url": url}, asyncio.Event(),
            grant=CapabilityGrant(
                fs_paths=(str(workspace),), net_allowlist=allowlist, timeout_s=60
            ),
            ctx=RunContext(agent_id="a", session_key="a:1", mode="chat", workspace=str(workspace)),
        )
    )


def test_a_real_child_reaches_a_declared_host_and_gets_the_body(
    tmp_path, http_server, monkeypatch
):
    monkeypatch.setenv("ACME_API_KEY", "sk-child-test")
    ws = tmp_path / "ws"
    ws.mkdir()
    res = _child_run(
        _spawn(tmp_path, secrets=("ACME_API_KEY",)),
        ws,
        (http_server.split(":")[0],),
        f"http://{http_server}/v1/hello",
    )
    text = "\n".join(getattr(b, "text", "") for b in res.content)
    assert not res.is_error, text
    assert "status=200" in text and "ok=True" in text
    # the credential was attached BY THE HOST — the child asked for it by name only
    assert "Bearer sk-child-test" in text


def test_a_real_child_reaches_a_host_it_never_declared(tmp_path, http_server, monkeypatch):
    """Open network, proven from inside a real child: an EMPTY allowlist and the request still
    lands. The child still has no socket of its own — the broker dialled — which is what keeps
    credential substitution host-side even with reach unrestricted."""
    monkeypatch.setenv("ACME_API_KEY", "sk-child-test")
    ws = tmp_path / "ws"
    ws.mkdir()
    res = _child_run(
        _spawn(tmp_path, secrets=("ACME_API_KEY",)), ws, (), f"http://{http_server}/v1/hello"
    )
    text = "\n".join(getattr(b, "text", "") for b in res.content)
    assert "status=200" in text and "ok=True" in text


# --- the docs must not lie about it -----------------------------------------


class _SkillText:
    """THE SKILL IS A DIRECTORY. SKILL.md is the procedure; the format detail lives in
    reference/*.md, read on demand. These checks ask "does the skill teach X", so reading spans
    the whole directory — a paragraph moving between its files must not disarm the check."""

    def __init__(self, root):
        self._root = root

    def read_text(self, encoding="utf-8"):
        parts = [(self._root / "SKILL.md").read_text(encoding=encoding)]
        parts += [p.read_text(encoding=encoding)
                  for p in sorted((self._root / "reference").glob("*.md"))]
        return chr(10).join(parts)


SKILL = _SkillText(
    Path(__file__).resolve().parents[2] / "agents/agent-builder/skills/build-agent"
)


def test_the_skill_teaches_the_route_that_actually_works():
    """A plugin author reads this and nothing else. If it still says 'assume no network', every
    agent built from it ships a tool that dies on the buyer's machine."""
    text = SKILL.read_text(encoding="utf-8")
    assert "[sandbox]" in text
    assert "outbound" in text and "fetch" in text
    assert "${" in text, "the placeholder form is the whole credential story"


def test_the_agent_builder_bundle_declares_no_network():
    """It is the one agent that ships with the product and it has no business calling out. A
    declaration appearing here later should be a deliberate, reviewed change."""
    p = Path(__file__).resolve().parents[2] / (
        "agents/agent-builder/plugins/agent-authoring/plugin.toml"
    )
    assert "sandbox" not in tomllib.loads(p.read_text(encoding="utf-8"))
