"""A plugin's URL can name a SETTING, and the setting resolves per account.

`net = ["${SERVICE_URL}"]` is how an author ships an agent that wraps a service each buyer runs
for themselves — a workstation on localhost for one, a rented GPU behind a proxy for the next.
The plugin writes `${SERVICE_URL}/api/x`; the host substitutes the CALLER's stored value at the
moment of the call. The declaration's job is naming which settings may appear in a URL — since
the 2026-09 lift it no longer restricts reach (plugins fetch any host; only the operator's own
deny/allow knobs refuse one).

What these tests pin:

  * the PER-ACCOUNT resolution — the same plugin code dials whatever host the current caller's
    setting holds, and an empty setting fails closed, naming the field;
  * the OPEN network — an undeclared host goes through, and the operator's deployment-level
    knobs are the only thing that refuses one;
  * the SECRET hygiene that outlived the lift — values substitute host-side, and a plugin can
    only name secrets its manifest declared.

The regression that first made this file exist: the broker validated scheme and host on the RAW
request, before substituting, so every placeholder URL was refused as schemeless — working for
the author in-process and dead for every buyer. Substitution must come first; still pinned here.
"""

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.application import run_context as rc
from agent_runtime.application.run_context import RunContext, set_run_context
from agent_runtime.domain.sandbox import CapabilityGrant
from agent_runtime.domain.sandbox_net import resolve_allowlist
from agent_runtime.infrastructure.tools.sandbox.fetch_broker import SandboxFetchBroker


class _Cfg:
    def __init__(self):
        self.sandbox_net_allow = ()
        self.sandbox_net_deny = ()
        self.sandbox_fetch_limits = {}


class _Echo(BaseHTTPRequestHandler):
    """Records the Authorization it was given, so a test can prove the credential ARRIVED —
    the plugin never held it, and "the host substituted nothing" would look identical from the
    plugin's side."""

    seen_auth = ""

    def do_GET(self):
        _Echo.seen_auth = self.headers.get("Authorization", "")
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def instance():
    """A real server on a real port. The port is not knowable until it is bound, which is the
    point: it stands in for a host only the user could have told us about."""
    srv = HTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Echo.seen_auth = ""
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def settings():
    """Installs a per-account settings reader and RESTORES the previous one.

    It is a module global rather than a contextvar (the account is already pinned per connection
    upstream), so a test that left one installed would silently feed every later test.
    """
    previous = rc._settings_reader
    stored: dict = {}
    rc.set_account_settings_reader(lambda agent_id: dict(stored))
    set_run_context(
        RunContext(
            agent_id="wrapper",
            session_key="s",
            mode="interactive",
            settings=("SERVICE_URL", "SERVICE_AUTH"),
        )
    )
    yield stored
    rc.set_account_settings_reader(previous)


def _broker(allowlist) -> SandboxFetchBroker:
    return SandboxFetchBroker(
        _Cfg(),
        plugin_id="wrapper-bridge",
        tool_name="probe",
        grant=CapabilityGrant(net_allowlist=allowlist, secrets=("SERVICE_AUTH",)),
        declared_secrets=("SERVICE_AUTH",),
    )


def _serve(broker, url, headers=None) -> dict:
    return asyncio.run(broker.serve({"url": url, "method": "GET", "headers": headers or {}})) or {}


# --- the declaration -------------------------------------------------------


def test_a_placeholder_survives_allowlist_resolution():
    """`resolve_allowlist` lowercases hosts, because DNS is case-insensitive. A setting NAME is
    not — `${Service_Url}` and `${SERVICE_URL}` are different settings — so the placeholder has
    to be recognised before that lowercasing, not after."""
    assert resolve_allowlist(["${SERVICE_URL}"], (), ()) == ("${SERVICE_URL}",)
    assert resolve_allowlist(["${Service_Url}"], (), ()) == ("${Service_Url}",)


def test_the_operator_s_strict_allow_binds_the_resolved_host(instance, settings):
    """The operator's knobs are the DEPLOYMENT's own fence and they survived the open network.
    A deployment pinned to specific hosts refuses a setting-resolved host like any other — the
    check runs on the address actually dialled, after substitution, so the indirection is not a
    way past it."""
    settings.update({"SERVICE_URL": instance})
    locked = _Cfg()
    locked.sandbox_net_allow = ("api.acme.com",)
    broker = SandboxFetchBroker(
        locked,
        plugin_id="wrapper-bridge",
        tool_name="probe",
        grant=CapabilityGrant(net_allowlist=("${SERVICE_URL}",), secrets=()),
    )

    refusal = _serve(broker, "${SERVICE_URL}/api/status")
    assert refusal.get("status") == 0
    assert "operator" in refusal.get("error", "")


def test_an_operator_deny_still_blocks_a_host_that_came_from_a_setting(instance, settings):
    """The deny list is the operator's hard stop — how a hosted daemon fences off its metadata
    endpoints. A user pointing a setting at a denied host must not be the way around it."""
    settings.update({"SERVICE_URL": instance})
    denied = _Cfg()
    denied.sandbox_net_deny = ("127.0.0.1",)
    broker = SandboxFetchBroker(
        denied,
        plugin_id="wrapper-bridge",
        tool_name="probe",
        grant=CapabilityGrant(net_allowlist=("${SERVICE_URL}",), secrets=()),
    )

    assert _serve(broker, "${SERVICE_URL}/api/status").get("status") == 0


# --- the user's host is reachable ------------------------------------------


def test_the_user_s_own_host_is_reached_and_the_credential_arrives(instance, settings):
    """The whole point, end to end: the author declared a NAME, the user supplied a HOST, and the
    plugin's request reaches it carrying a secret the plugin itself never saw."""
    settings.update({"SERVICE_URL": instance, "SERVICE_AUTH": "Bearer sk-per-account"})
    broker = _broker(resolve_allowlist(["${SERVICE_URL}"], (), ()))

    result = _serve(
        broker, "${SERVICE_URL}/api/status", {"Authorization": "${SERVICE_AUTH}"}
    )

    assert result.get("status") == 200, result.get("error")
    assert _Echo.seen_auth == "Bearer sk-per-account"


def test_the_placeholder_url_dials_the_caller_s_own_host(instance, settings):
    """One agent, one declaration, one daemon — and the DESTINATION is still per tenant. The
    same literal `${SERVICE_URL}/api/status` in plugin code goes wherever the CALLER's setting
    points, resolved at the moment of the call. This is the per-account mechanism, and it is
    untouched by the open network: reach is unrestricted, but a placeholder still means "this
    caller's value", never "somebody's"."""
    broker = _broker(resolve_allowlist(["${SERVICE_URL}"], (), ()))

    settings.update({"SERVICE_URL": instance})
    assert _serve(broker, "${SERVICE_URL}/api/status").get("status") == 200

    # The next caller's setting points at a DIFFERENT server; the identical plugin code now
    # dials that one — proven by which server records the hit.
    class _Second(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self):
            _Second.hits += 1
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    second = HTTPServer(("127.0.0.1", 0), _Second)
    threading.Thread(target=second.serve_forever, daemon=True).start()
    try:
        settings["SERVICE_URL"] = f"http://127.0.0.1:{second.server_port}"
        assert _serve(broker, "${SERVICE_URL}/api/status").get("status") == 200
        assert _Second.hits == 1
    finally:
        second.shutdown()


# --- the open network, and what still refuses -------------------------------


def test_any_other_host_is_reachable_too(instance, settings):
    """The 2026-09 lift, from the placeholder plugin's seat: declaring `${SERVICE_URL}` does not
    CONFINE the plugin to it. A literal URL to some other host — a model card on HF, a reference
    workflow on a docs site — goes through. Reach restriction is gone; the declaration's only
    remaining meaning is which ${SETTING}s may appear in a URL."""
    settings.update({"SERVICE_URL": "http://192.0.2.7:9"})  # points elsewhere entirely
    broker = _broker(resolve_allowlist(["${SERVICE_URL}"], (), ()))

    result = _serve(broker, f"{instance}/api/status")  # a host the manifest never named

    assert result.get("status") == 200, result.get("error")


def test_an_empty_setting_still_fails_closed_and_names_the_field(settings):
    """Unconfigured still fails CLOSED — with the setting empty the placeholder cannot resolve,
    so the request has no address; open reach does not invent one.

    It must also fail LEGIBLY: the refusal names the SETTING, because that is the thing the
    user can fix — not a scheme error that points at the plugin."""
    broker = _broker(resolve_allowlist(["${SERVICE_URL}"], (), ()))

    result = _serve(broker, "${SERVICE_URL}/api/status")

    assert result.get("status") == 0
    assert "SERVICE_URL" in result.get("error", "")


def test_a_plugin_cannot_ask_for_a_name_it_never_declared(instance, settings):
    """The stray-placeholder guard has to keep working now that host settings are legal in a URL.
    Widening "what may appear in a request" to declared secrets PLUS declared hosts must not
    widen it to anything else, or a plugin could fish for a name it never disclosed."""
    settings.update({"SERVICE_URL": instance, "OTHER_AGENTS_KEY": "sk-not-yours"})
    broker = _broker(resolve_allowlist(["${SERVICE_URL}"], (), ()))

    result = _serve(
        broker, "${SERVICE_URL}/api/status", {"X-Steal": "${OTHER_AGENTS_KEY}"}
    )

    assert result.get("status") == 0
    assert "OTHER_AGENTS_KEY" in result.get("error", "")
    assert "sk-not-yours" not in json.dumps(result)


# --- the two paths agree ---------------------------------------------------


def test_the_sandboxed_path_resolves_a_setting_the_same_way_the_direct_path_does(
    instance, settings
):
    """The bug class this file is really about: a plugin that works in-process and fails
    sandboxed gets blamed on the sandbox. The broker used to read `os.environ` directly while
    `net.outbound` went through the account-aware resolver, so a value stored PER ACCOUNT was
    invisible to exactly the plugins that are sandboxed."""
    settings.update({"SERVICE_URL": instance, "SERVICE_AUTH": "Bearer sk-stored-per-account"})

    from agent_runtime.application.run_context import current_setting_value

    direct = current_setting_value("SERVICE_AUTH")
    broker = _broker(resolve_allowlist(["${SERVICE_URL}"], (), ()))
    _serve(broker, "${SERVICE_URL}/api/status", {"Authorization": "${SERVICE_AUTH}"})

    assert direct == "Bearer sk-stored-per-account"
    assert _Echo.seen_auth == direct


# --- the whole thing, in a real child ---------------------------------------
#
# Everything above drives the broker directly, which proves the rule but not the plumbing. This
# spawns a REAL sandboxed subprocess running the REAL comfy-bridge plugin — the shipped file, not
# a fixture — with no socket of its own, and asks it to probe an instance whose address exists
# only in a per-account setting. If the mechanism is going to fail for a buyer, it fails here.

from agent_runtime.infrastructure.tools.sandbox.subprocess_backend import (  # noqa: E402
    SubprocessPluginSandbox,
)

COMFY_BRIDGE = (
    Path(__file__).resolve().parents[2] / "agents" / "comfy-artchitect" / "plugins" / "comfy-bridge"
)


class _Stats(BaseHTTPRequestHandler):
    """Enough of ComfyUI for `comfy_probe`: `/api/system_stats` and `/api/prompt`."""

    seen: list = []

    def do_GET(self):
        _Stats.seen.append((self.path, self.headers.get("Authorization", "")))
        body = (
            json.dumps(
                {
                    "system": {
                        "comfyui_version": "0.3.99",
                        "python_version": "3.12.0 (main)",
                        "pytorch_version": "2.5.1",
                    },
                    "devices": [
                        {"name": "NVIDIA RTX 4090", "vram_free": 21 * 1024**3, "vram_total": 24 * 1024**3}
                    ],
                }
            )
            if "system_stats" in self.path
            else json.dumps({"exec_info": {"queue_remaining": 0}})
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _BridgeTool:
    """What the loader needs to spawn the plugin: where it lives and what it may be given."""

    name = "comfy_probe"
    _plugin_entry = "comfy_bridge:register"
    _plugin_root = str(COMFY_BRIDGE)
    _sandbox_secrets = ("COMFYUI_AUTH", "COMFYUI_AUTH2", "COMFYUI_AUTH3")


@pytest.fixture
def comfyui():
    srv = HTTPServer(("127.0.0.1", 0), _Stats)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Stats.seen = []
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def comfy_settings():
    """This agent's own settings, declared under this agent's own name.

    A separate fixture rather than a reuse of `settings`, because the RUN CONTEXT'S DECLARATION
    is load-bearing: `current_setting_value` reads the per-account store only for a name the
    running agent declared, and falls back to the machine-wide variable for anything else. That
    is the rule that stops one agent reading another's key, so a context declaring the wrong
    names is not a detail — it is the difference between a per-tenant value and an operator's
    environment.
    """
    previous = rc._settings_reader
    stored: dict = {}
    rc.set_account_settings_reader(lambda agent_id: dict(stored))
    set_run_context(
        RunContext(
            agent_id="comfy-artchitect",
            session_key="comfy-artchitect:1",
            mode="chat",
            settings=("COMFYUI_URL", "COMFYUI_AUTH", "COMFYUI_AUTH2", "COMFYUI_AUTH3"),
        )
    )
    yield stored
    rc.set_account_settings_reader(previous)


@pytest.mark.skipif(not COMFY_BRIDGE.is_dir(), reason="comfy-artchitect is not in this tree")
def test_a_real_sandboxed_plugin_reaches_the_instance_named_only_in_a_setting(
    tmp_path, comfyui, comfy_settings
):
    """The shipped plugin, sandboxed, reaching a host it cannot see.

    `comfy_bridge.py` contains no host and no credential — it writes `${COMFYUI_URL}/api/...` and
    `${COMFYUI_AUTH}`. The address lives in this account's settings, in the parent. The child has
    no socket, so the request is brokered back out; the parent resolves both names and dials.

    This is the case the platform could not do before: an author ships one agent, and each buyer
    points it at their own machine.
    """
    comfy_settings.update({"COMFYUI_URL": comfyui, "COMFYUI_AUTH": "Bearer sk-this-account"})
    ws = tmp_path / "ws"
    ws.mkdir()

    sandbox = SubprocessPluginSandbox(_Cfg())
    sandbox.register("comfy-bridge", "comfy_probe", _BridgeTool())
    result = asyncio.run(
        sandbox.run_tool(
            "comfy-bridge",
            "comfy_probe",
            "c1",
            {},
            asyncio.Event(),
            # `secrets` IS LEFT EMPTY, exactly as DefaultCapabilityResolver leaves it. That field
            # injects values into the CHILD'S ENVIRONMENT, and the entire point of declaring a
            # credential by name is that the value never arrives there. What the plugin declared
            # rides on the tool (`_sandbox_secrets`) and is substituted by the broker, in the
            # parent, into the outbound request.
            grant=CapabilityGrant(
                fs_paths=(str(ws),),
                net_allowlist=("${COMFYUI_URL}",),
                timeout_s=60,
            ),
            ctx=RunContext(
                agent_id="comfy-artchitect",
                session_key="comfy-artchitect:1",
                mode="chat",
                workspace=str(ws),
                settings=("COMFYUI_URL", "COMFYUI_AUTH"),
            ),
        )
    )

    text = "\n".join(getattr(b, "text", "") for b in result.content)
    assert not result.is_error, text
    assert "ComfyUI 0.3.99" in text, text
    assert "21 GB free of 24 GB" in text, text

    # The credential was attached BY THE HOST. The child asked for it by name and never held it.
    paths = [p for p, _ in _Stats.seen]
    assert "/api/system_stats" in paths, paths
    assert all(auth == "Bearer sk-this-account" for _, auth in _Stats.seen), _Stats.seen


@pytest.mark.skipif(not COMFY_BRIDGE.is_dir(), reason="comfy-artchitect is not in this tree")
def test_the_same_plugin_reaches_nothing_when_the_setting_is_empty(
    tmp_path, comfyui, comfy_settings
):
    """Unconfigured fails CLOSED, and says which field to fill in.

    The plugin is identical and the server is running — only the account's setting is missing. The
    grant resolves to nothing, so there is no host to dial and the tool reports that rather than
    reaching a default."""
    ws = tmp_path / "ws"
    ws.mkdir()

    sandbox = SubprocessPluginSandbox(_Cfg())
    sandbox.register("comfy-bridge", "comfy_probe", _BridgeTool())
    result = asyncio.run(
        sandbox.run_tool(
            "comfy-bridge",
            "comfy_probe",
            "c1",
            {},
            asyncio.Event(),
            grant=CapabilityGrant(
                fs_paths=(str(ws),), net_allowlist=("${COMFYUI_URL}",), timeout_s=60
            ),
            ctx=RunContext(
                agent_id="comfy-artchitect",
                session_key="comfy-artchitect:1",
                mode="chat",
                workspace=str(ws),
                settings=("COMFYUI_URL",),
            ),
        )
    )

    text = "\n".join(getattr(b, "text", "") for b in result.content)
    assert "COMFYUI_URL" in text, text
    assert not _Stats.seen, "the server was reached with no setting configured"


# --- a user-hosted URL that carries its own credentials ---------------------
# The address a person pastes for their own service is whatever their provider handed them —
# for vast/RunPod that is `http://host:port/?token=abc`, a full URL with the token in the query.
# A plugin writes `${SERVICE_URL}/api/x` and cannot see the value, so it cannot move the token
# out of the way; the broker folds it, so the user pastes ONE thing and never meets a header.


def test_a_url_with_an_embedded_token_folds_onto_the_request(instance, settings):
    """`${SERVICE_URL}/api/status` with the setting = `<instance>/?token=abc` must dial
    `<instance>/api/status?token=abc` — token AFTER the path, not the broken
    `<instance>/?token=abc/api/status` naive substitution would produce."""
    settings.update({"SERVICE_URL": f"{instance}/?token=sk-in-the-url"})
    broker = _broker(resolve_allowlist(["${SERVICE_URL}"], (), ()))

    resolved = broker._resolve_url("${SERVICE_URL}/api/status")

    assert resolved == f"{instance}/api/status?token=sk-in-the-url"


def test_a_plain_url_is_left_exactly_alone(instance, settings):
    """No query, no userinfo — nothing to fold, and the result is the ordinary join. The folding
    path must not perturb the common localhost case."""
    settings.update({"SERVICE_URL": instance})
    broker = _broker(resolve_allowlist(["${SERVICE_URL}"], (), ()))

    assert broker._resolve_url("${SERVICE_URL}/api/status") == f"{instance}/api/status"


def test_a_trailing_slash_in_the_setting_does_not_double(instance, settings):
    settings.update({"SERVICE_URL": f"{instance}/"})
    broker = _broker(resolve_allowlist(["${SERVICE_URL}"], (), ()))

    assert broker._resolve_url("${SERVICE_URL}/api/status") == f"{instance}/api/status"


def test_userinfo_in_the_setting_is_carried_onto_the_request(settings):
    settings.update({"SERVICE_URL": "http://user:pass@10.0.0.5:8188"})
    broker = _broker(resolve_allowlist(["${SERVICE_URL}"], (), ()))

    assert (
        broker._resolve_url("${SERVICE_URL}/api/status")
        == "http://user:pass@10.0.0.5:8188/api/status"
    )


def test_the_embedded_token_actually_reaches_the_server(instance, settings):
    """End to end through the broker: the folded token arrives as a query param the server sees,
    with NO Authorization header set — which is the whole point (the user filled in only a URL)."""
    settings.update({"SERVICE_URL": f"{instance}/?token=sk-query-only"})
    broker = _broker(resolve_allowlist(["${SERVICE_URL}"], (), ()))

    result = _serve(broker, "${SERVICE_URL}/api/status")

    assert result.get("status") == 200, result.get("error")
    assert result.get("url", "").endswith("?token=sk-query-only")
