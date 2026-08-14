"""OAuth — signing in to a third-party service, once, per agent.

The reason this exists: most services worth connecting to have no API key to paste. You sign in.
So `[[settings]]` alone would have left "an app I signed up to" unbuildable.

What is pinned here is the part that has to be right or it is worse than nothing:

  * `state` is checked, so a callback nobody started is refused
  * PKCE, so a code intercepted on the loopback redirect is useless on its own
  * expiry is absolute and refreshed BEFORE it bites
  * two agents connecting the same provider are two different sign-ins
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from agent_runtime.application.services.oauth_service import FLOW_TTL_S, OAuthService
from agent_runtime.domain.oauth_connection import OAuthDecl, OAuthTokens
from agent_runtime.infrastructure.auth.file_token_store import FileTokenStore

CLASSIC = OAuthDecl(
    name="myhealth",
    authorize_url="https://accounts.health.app/authorize",
    token_url="https://accounts.health.app/token",
    scopes=("read:records",),
    client_id="health-client",
)
DISCOVERED = OAuthDecl(name="myhealth", server="https://api.health.app", client_id="c")
REDIRECT = "http://127.0.0.1:8765/oauth/callback"


class _Http:
    """Records what the service asked for and answers with whatever the test set up."""

    def __init__(self, token_payload=None, metadata=None):
        self.token_payload = token_payload or {"access_token": "at-1", "expires_in": 3600}
        self.metadata = metadata or {
            "authorization_endpoint": "https://api.health.app/authorize",
            "token_endpoint": "https://api.health.app/token",
        }
        self.posts: list = []
        self.gets: list = []

    async def get_json(self, url):
        self.gets.append(url)
        return self.metadata

    async def post_form(self, url, data, client_secret=""):
        self.posts.append((url, dict(data), client_secret))
        return self.token_payload


def _service(http=None, store=None, now=None):
    clock = now or (lambda: 1000.0)
    return OAuthService(
        http=http or _Http(),
        store=store or FileTokenStore(Path("/nonexistent")),
        resolve_setting=lambda agent_id, value: value,  # literals in these tests
        now=clock,
    )


def _store(tmp_path) -> FileTokenStore:
    return FileTokenStore(tmp_path / "oauth")


def _url_params(url: str) -> dict:
    from urllib.parse import parse_qs, urlsplit

    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# ── starting ────────────────────────────────────────────────────────────────
def test_begin_returns_an_authorize_url_with_pkce(tmp_path):
    svc = _service(store=_store(tmp_path))
    url = asyncio.run(svc.begin("trader", CLASSIC, REDIRECT))
    params = _url_params(url)
    assert url.startswith("https://accounts.health.app/authorize?")
    assert params["response_type"] == "code" and params["client_id"] == "health-client"
    assert params["redirect_uri"] == REDIRECT and params["scope"] == "read:records"
    assert params["code_challenge_method"] == "S256" and len(params["code_challenge"]) > 20
    assert params["state"]


def test_the_verifier_itself_never_appears_in_the_url(tmp_path):
    """That is the whole point of S256: the provider sees a hash, so a redirect cannot be
    replayed by whoever managed to read it."""
    svc = _service(store=_store(tmp_path))
    url = asyncio.run(svc.begin("trader", CLASSIC, REDIRECT))
    verifier = next(iter(svc._pending.values())).verifier
    assert verifier not in url


def test_endpoints_can_be_discovered_from_a_server(tmp_path):
    http = _Http()
    svc = _service(http=http, store=_store(tmp_path))
    url = asyncio.run(svc.begin("trader", DISCOVERED, REDIRECT))
    assert http.gets == ["https://api.health.app/.well-known/oauth-authorization-server"]
    assert url.startswith("https://api.health.app/authorize?")


def test_a_declaration_with_no_endpoints_says_so(tmp_path):
    svc = _service(store=_store(tmp_path))
    with pytest.raises(ValueError, match="authorize_url"):
        asyncio.run(svc.begin("trader", OAuthDecl(name="x"), REDIRECT))


def test_a_missing_client_id_names_the_fix(tmp_path):
    """A classic provider needs an app registered up front. Saying 'needs a client_id' beats a
    provider error page the user cannot act on."""
    svc = _service(store=_store(tmp_path))
    decl = OAuthDecl(name="x", authorize_url="https://a/x", token_url="https://a/t")
    with pytest.raises(ValueError, match="client_id"):
        asyncio.run(svc.begin("trader", decl, REDIRECT))


# ── finishing ───────────────────────────────────────────────────────────────
def test_a_completed_sign_in_stores_a_token(tmp_path):
    store = _store(tmp_path)
    http = _Http({"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})
    svc = _service(http=http, store=store)
    url = asyncio.run(svc.begin("trader", CLASSIC, REDIRECT))
    state = _url_params(url)["state"]

    assert asyncio.run(svc.complete(state, "the-code"))["connected"] is True
    saved = store.load("trader", "myhealth")
    assert saved.access_token == "at-1" and saved.refresh_token == "rt-1"
    assert saved.expires_at == 1000.0 + 3600  # ABSOLUTE, computed once


def test_the_exchange_sends_the_verifier(tmp_path):
    http = _Http()
    svc = _service(http=http, store=_store(tmp_path))
    url = asyncio.run(svc.begin("trader", CLASSIC, REDIRECT))
    verifier = next(iter(svc._pending.values())).verifier
    asyncio.run(svc.complete(_url_params(url)["state"], "the-code"))
    assert http.posts[0][1]["code_verifier"] == verifier
    assert http.posts[0][1]["grant_type"] == "authorization_code"


def test_an_unknown_state_is_refused(tmp_path):
    """THE CSRF CHECK. Without it, anyone who can send the user's browser to our callback with a
    code of their choosing gets that identity stored as the user's."""
    svc = _service(store=_store(tmp_path))
    with pytest.raises(ValueError, match="unknown or already-used"):
        asyncio.run(svc.complete("not-a-state", "code"))


def test_a_state_cannot_be_replayed(tmp_path):
    svc = _service(store=_store(tmp_path))
    url = asyncio.run(svc.begin("trader", CLASSIC, REDIRECT))
    state = _url_params(url)["state"]
    asyncio.run(svc.complete(state, "code"))
    with pytest.raises(ValueError, match="unknown or already-used"):
        asyncio.run(svc.complete(state, "code"))


def test_an_abandoned_sign_in_expires(tmp_path):
    clock = {"t": 1000.0}
    svc = _service(store=_store(tmp_path), now=lambda: clock["t"])
    url = asyncio.run(svc.begin("trader", CLASSIC, REDIRECT))
    clock["t"] += FLOW_TTL_S + 1
    with pytest.raises(ValueError, match="expired"):
        asyncio.run(svc.complete(_url_params(url)["state"], "code"))


def test_a_provider_that_returns_no_token_is_an_error_not_a_connection(tmp_path):
    svc = _service(http=_Http({"error": "invalid_grant"}), store=_store(tmp_path))
    url = asyncio.run(svc.begin("trader", CLASSIC, REDIRECT))
    with pytest.raises(RuntimeError, match="no access token"):
        asyncio.run(svc.complete(_url_params(url)["state"], "code"))


# ── using ───────────────────────────────────────────────────────────────────
def test_a_live_token_is_returned_as_is(tmp_path):
    store = _store(tmp_path)
    store.save("trader", "myhealth", OAuthTokens(access_token="at-1", expires_at=9999.0))
    assert asyncio.run(_service(store=store).token("trader", CLASSIC)) == "at-1"


def test_not_connected_is_empty_rather_than_an_exception(tmp_path):
    """The normal state of a fresh install. The caller's own "connect it in settings" beats a
    stack trace."""
    assert asyncio.run(_service(store=_store(tmp_path)).token("trader", CLASSIC)) == ""


def test_a_token_about_to_expire_is_refreshed_before_it_bites(tmp_path):
    """Refreshed on the skew, not on the deadline: a request that leaves at 11:59:59.8 for a
    token expiring at 12:00:00 is a 401 nobody can reproduce."""
    store = _store(tmp_path)
    store.save("trader", "myhealth", OAuthTokens("old", refresh_token="rt-1", expires_at=1030.0))
    http = _Http({"access_token": "new", "expires_in": 3600})
    assert asyncio.run(_service(http=http, store=store).token("trader", CLASSIC)) == "new"
    assert http.posts[0][1]["grant_type"] == "refresh_token"
    assert store.load("trader", "myhealth").access_token == "new"


def test_a_refresh_that_omits_a_new_refresh_token_keeps_the_old_one(tmp_path):
    """Most providers re-issue; some do not, and dropping it would silently make the connection
    single-use."""
    store = _store(tmp_path)
    store.save("trader", "myhealth", OAuthTokens("old", refresh_token="rt-1", expires_at=1030.0))
    svc = _service(http=_Http({"access_token": "new", "expires_in": 60}), store=store)
    asyncio.run(svc.token("trader", CLASSIC))
    assert store.load("trader", "myhealth").refresh_token == "rt-1"


def test_an_expired_token_with_no_refresh_disconnects(tmp_path):
    """The honest state is 'not connected'. Handing back a token that will 401 sends the user
    hunting through the wrong part of the app."""
    store = _store(tmp_path)
    store.save("trader", "myhealth", OAuthTokens("old", expires_at=1010.0))
    assert asyncio.run(_service(store=store).token("trader", CLASSIC)) == ""
    assert store.load("trader", "myhealth") is None


def test_a_failed_refresh_raises_rather_than_looking_disconnected(tmp_path):
    """A broken connection and an absent one need different fixes from the user."""
    store = _store(tmp_path)
    store.save("trader", "myhealth", OAuthTokens("old", refresh_token="rt", expires_at=1010.0))
    svc = _service(http=_Http({"error": "invalid_grant"}), store=store)
    with pytest.raises(RuntimeError, match="could not refresh"):
        asyncio.run(svc.token("trader", CLASSIC))


def test_the_synchronous_reader_never_hands_out_a_stale_token(tmp_path):
    """`${oauth:…}` runs inside a plugin's fetch and cannot await a refresh, so a stale token
    reads as empty and the provider's own 401 is what surfaces."""
    store = _store(tmp_path)
    store.save("trader", "myhealth", OAuthTokens("old", refresh_token="rt", expires_at=1010.0))
    assert _service(store=store).stored_token("trader", "myhealth") == ""
    store.save("trader", "myhealth", OAuthTokens("fresh", expires_at=99999.0))
    assert _service(store=store).stored_token("trader", "myhealth") == "fresh"


# ── scope ───────────────────────────────────────────────────────────────────
def test_two_agents_are_two_different_sign_ins(tmp_path):
    store = _store(tmp_path)
    store.save("cost", "myhealth", OAuthTokens("token-cost", expires_at=9999.0))
    store.save("provision", "myhealth", OAuthTokens("token-provision", expires_at=9999.0))
    svc = _service(store=store)
    assert asyncio.run(svc.token("cost", CLASSIC)) == "token-cost"
    assert asyncio.run(svc.token("provision", CLASSIC)) == "token-provision"


def test_disconnecting_one_agent_leaves_the_other_signed_in(tmp_path):
    store = _store(tmp_path)
    store.save("cost", "myhealth", OAuthTokens("a", expires_at=9999.0))
    store.save("provision", "myhealth", OAuthTokens("b", expires_at=9999.0))
    _service(store=store).disconnect("cost", "myhealth")
    assert store.load("cost", "myhealth") is None
    assert store.load("provision", "myhealth").access_token == "b"


def test_status_reports_what_is_connected(tmp_path):
    store = _store(tmp_path)
    store.save("trader", "myhealth", OAuthTokens("a", expires_at=9999.0, account="me@health.app"))
    (row,) = _service(store=store).status("trader", [CLASSIC])
    assert row == {
        "name": "myhealth",
        "connected": True,
        "account": "me@health.app",
        "scopes": ["read:records"],
    }


def test_a_corrupt_record_reads_as_disconnected(tmp_path):
    store = _store(tmp_path)
    path = tmp_path / "oauth" / "trader" / "myhealth.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert store.load("trader", "myhealth") is None
