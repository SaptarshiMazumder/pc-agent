import asyncio
import os
import sys
import uuid
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


@pytest.fixture(autouse=True)
def _app_secret_local(monkeypatch):
    """Point the accounts service's startup at its LOCAL secret source (the ambient environment)
    for every test. The service refuses to boot without a declared secret source (app.py's
    _startup) — in production that is a Secrets Manager id; in a test there is no Secrets Manager,
    so 'local' declares the env the test already sets as the source. Without this, every test that
    builds the FastAPI app via TestClient fails at startup with AppSecretUnavailable. Autouse and
    harmless where no app is built: it only sets an env var. Uses setdefault semantics via
    monkeypatch so a test that wants to exercise the strict path can still override it."""
    monkeypatch.setenv("AGENTD_APP_SECRET_ID", "local")


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


@pytest.fixture(autouse=True)
def _postgres_backend_when_asked(monkeypatch):
    """Run the WHOLE suite against Postgres when `AGENTD_TEST_DB=postgres`.

    THIS IS THE EQUIVALENCE GATE FOR THE SQLITE MIGRATION. The suites in tests/unit already
    encode what the money code is supposed to do; pointing them at the other backend asks the
    only question that matters — does it still do that? — without writing a second set of
    assertions that could drift from the first.

    ONE SCHEMA PER TEST, because these suites each expect a virgin database (they assert on
    balances and row counts) and on Postgres they would otherwise share one. The schema is
    created before the test and dropped after, so a failure cannot make the next test lie.

    THE UNPOOLED HOST IS REQUIRED: Neon's pooled endpoint (PgBouncer) rejects the `options`
    startup parameter outright, and `options=-csearch_path` is how the isolation above is
    delivered. Production is unaffected — it uses the default schema and the pooled endpoint.
    """
    # NOTE the `yield` rather than a bare `return`: this function contains a yield, so pytest
    # treats it as a generator fixture, and returning without yielding fails every test that
    # requests it with "fixture did not yield a value" — which, being autouse, is all of them.
    if (os.environ.get("AGENTD_TEST_DB") or "").strip().lower() != "postgres":
        yield
        return
    base = (os.environ.get("AGENTD_TEST_DATABASE_URL") or "").strip()
    if not base:
        pytest.skip("AGENTD_TEST_DB=postgres but AGENTD_TEST_DATABASE_URL is not set")

    import psycopg

    schema = f"t_{uuid.uuid4().hex[:12]}"
    unpooled = base.replace("-pooler.", ".")
    sep = "&" if "?" in unpooled else "?"
    monkeypatch.setenv("DATABASE_URL", f"{unpooled}{sep}options=-csearch_path%3D{schema}")

    accounts_dir = _ROOT / "accounts"
    if accounts_dir.is_dir() and str(accounts_dir) not in sys.path:
        sys.path.insert(0, str(accounts_dir))

    admin = psycopg.connect(unpooled, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        # The pool is a process singleton and caches the URL it was built with, so a pool left
        # open by the previous test would serve this one the previous test's schema.
        import postgres_connection_pool

        postgres_connection_pool.close()
        yield
        postgres_connection_pool.close()
    finally:
        admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.close()


@pytest.fixture(autouse=True)
def _secrets_manager_not_required_in_tests(monkeypatch):
    """Let the accounts app boot without reaching AWS.

    The service refuses to start unless `AGENTD_APP_SECRET_ID` names a secret it can read —
    deliberately, so a deployment can never run on whatever env vars happen to be lying around
    (accounts/app_secret_loader.py). That rule is correct in production and fatal in a test
    suite: every test that starts the app through its startup hook would need AWS credentials
    and a network, and would be testing Secrets Manager rather than the thing under test.

    So the id is supplied and the FETCH is neutered, rather than the rule being relaxed. A test
    that genuinely wants to exercise the loader overrides this by monkeypatching it back — and
    the production path is untouched, which is the point: the guard still fails a real service
    that is missing its configuration.
    """
    monkeypatch.setenv("AGENTD_APP_SECRET_ID", "tests/app-secret")
    accounts_dir = _ROOT / "accounts"
    if accounts_dir.is_dir() and str(accounts_dir) not in sys.path:
        sys.path.insert(0, str(accounts_dir))
    try:
        import app_secret_loader
    except ImportError:  # the accounts service is not part of this test's world
        return
    monkeypatch.setattr(
        app_secret_loader.AppSecretLoader, "load_into_environ", lambda self: [], raising=False
    )


def pytest_collection_modifyitems(config, items):
    for item in items:
        try:
            rel = Path(str(item.fspath)).resolve().relative_to(_TESTS_DIR)
        except ValueError:
            continue
        tier = rel.parts[0] if rel.parts else ""
        if tier in _TIERS:
            item.add_marker(getattr(pytest.mark, tier))
