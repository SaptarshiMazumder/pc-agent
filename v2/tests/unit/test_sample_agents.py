"""The sample agents must stay correct, and their built UI must stay current.

Samples are REFERENCE MATERIAL: Agent Builder reads them to learn what a finished agent looks
like, and whatever they do becomes what it does. That makes a stale sample worse than no sample —
it teaches the wrong shape with our authority behind it, and nobody notices, because nothing runs
them. Samples ARE registered — an exemplar nobody can run rots exactly like the agents this repo
spent a day fixing — but flagged `sample=True` so a client keeps them in their own section
instead of mixed into the user's own agents.

So the samples are held to the same validator every authored agent is, plus one check only they
need: the built `ui/` must not be older than the React source it came from. A sample whose
committed build predates its source ships one thing and documents another, and the difference is
invisible until someone opens the window.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "agents"
        / "agent-builder"
        / "plugins"
        / "agent-authoring"
    ),
)

import pytest
from agent_runtime.domain.events import APP_FACING_EVENTS, MESSAGE_UPDATE_KINDS
from agent_runtime.infrastructure.agents import FileAgentRegistry
from agent_runtime.presentation.gateway import APP_SCOPED_METHODS, PROVIDER_ENV_KEYS

from agent_authoring.application.validate_agent_service import ValidateAgentService
from agent_authoring.domain.agent_layout_rules import AgentLayoutRules
from agent_authoring.domain.declaration_rules import DeclarationRules
from agent_authoring.domain.packageability_rules import PackageabilityRules
from agent_authoring.domain.sandbox_rules import SandboxRules
from agent_authoring.domain.ui_component import UiComponents
from agent_authoring.domain.ui_rules import UiRules
from agent_authoring.infrastructure.agent_dir_reader import AgentDirReader

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "agents" / "samples"

SAMPLE_IDS = sorted(p.name for p in SAMPLES.iterdir() if (p / "agent.toml").is_file()) if SAMPLES.is_dir() else []


def _validator():
    """The real rule set against the REAL registry — the one the daemon builds.

    Not a registry rooted at `agents/samples/`: pointing one there synthesizes a `main` and
    mkdir's `agents/samples/main/workspace/`, so the test would create a stray agent inside the
    tree it is checking. Samples are registered now, so the honest check is the shipped path.
    """
    from agent_runtime.config import load_config

    registry = FileAgentRegistry(load_config())
    components = UiComponents()
    return ValidateAgentService(
        AgentDirReader(registry),
        AgentLayoutRules(),
        PackageabilityRules(),
        SandboxRules(),
        UiRules(
            events=APP_FACING_EVENTS,
            kinds=MESSAGE_UPDATE_KINDS,
            methods=frozenset(APP_SCOPED_METHODS),
            sdk_methods=frozenset(),
            components=components.all(),
        ),
        declaration_rules=DeclarationRules(provider_keys=PROVIDER_ENV_KEYS),
    )


def test_there_is_at_least_one_sample():
    """If this fails, the samples were moved or deleted and every test below silently passed
    by iterating over nothing."""
    assert SAMPLE_IDS, f"no sample agents found under {SAMPLES}"


@pytest.mark.parametrize("agent_id", SAMPLE_IDS)
def test_a_sample_has_no_validation_errors(agent_id):
    """Samples teach by example, so an error in one is an error taught to every agent built
    from it."""
    report = _validator().validate(agent_id)
    errors = [f for f in report.findings if f.is_error]
    assert not errors, f"{agent_id}:\n" + "\n".join(f"  {f.code}: {f.message}" for f in errors)


@pytest.mark.parametrize("agent_id", SAMPLE_IDS)
def test_a_sample_is_registered_but_flagged(agent_id):
    """Runnable, so it cannot rot — and flagged, so no client shows it beside the user's own.

    Both halves matter. Unregistered, nobody ever executes it and it drifts into teaching a
    shape that no longer works. Unflagged, our reference implementations appear in every user's
    agent list as though they built them."""
    from agent_runtime.config import load_config

    registry = FileAgentRegistry(load_config())
    assert agent_id in registry.list_ids(), f"{agent_id} is not registered — it can never be run"
    assert registry.get(agent_id).sample is True, f"{agent_id} is not flagged as a sample"


def test_a_real_agent_is_not_flagged_as_a_sample():
    """The flag has to distinguish. If everything were flagged, the Samples section would hold
    the user's own work."""
    from agent_runtime.config import load_config

    registry = FileAgentRegistry(load_config())
    real = [i for i in registry.list_ids() if i not in SAMPLE_IDS]
    assert real, "no real agents to compare against"
    assert not [i for i in real if registry.get(i).sample]


@pytest.mark.parametrize("agent_id", SAMPLE_IDS)
def test_a_react_samples_build_is_not_stale(agent_id):
    """The committed `ui/` must be at least as new as the `app/src` it was built from.

    Same failure as shipping an installer around last week's daemon: the source says one thing,
    the artifact does another, `npm run dev` looks right and the built window does not. Caught by
    mtime rather than by version, because the version rarely changes between edits — which is
    exactly when this goes wrong.
    """
    agent = SAMPLES / agent_id
    src = agent / "app" / "src"
    built = agent / "ui" / "index.html"
    if not src.is_dir():
        pytest.skip(f"{agent_id} has no React app")
    assert built.is_file(), f"{agent_id}: app/src exists but ui/index.html does not — run `npm run build` in app/"
    newest = max(p.stat().st_mtime for p in src.rglob("*") if p.is_file())
    assert built.stat().st_mtime >= newest, (
        f"{agent_id}: ui/ is OLDER than app/src — the committed build is stale. "
        f"cd agents/samples/{agent_id}/app && npm run build"
    )


@pytest.mark.parametrize("agent_id", SAMPLE_IDS)
def test_a_react_sample_uses_relative_asset_urls(agent_id):
    """`base: './'` in vite.config.ts, checked in the OUTPUT rather than the config.

    The app is served under `/apps/<id>/`. An absolute `/assets/…` asks the daemon's root for a
    file that is not there, so the page loads, the console shows a 404 nobody is watching, and
    the window stays blank."""
    built = SAMPLES / agent_id / "ui" / "index.html"
    if not built.is_file():
        pytest.skip(f"{agent_id} has no built UI")
    html = built.read_text(encoding="utf-8")
    assert 'src="/assets/' not in html and 'href="/assets/' not in html, (
        f"{agent_id}: built with absolute asset URLs — set `base: './'` in vite.config.ts"
    )
