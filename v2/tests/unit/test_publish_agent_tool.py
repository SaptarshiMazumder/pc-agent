"""publish_agent's validation gate must SAY WHAT IS WRONG.

The trigger: the gate refused a real publish with

    refusing to publish: validate_agent reports errors. Fix them, then publish.

and nothing else. It read the report through `getattr(report, "message", "")` — an attribute
`Report` has never had — so the defensive default won every time and the whole report was
silently dropped. The author is told to fix something and not told what, and the only way
forward is to guess that a second tool (`validate_agent`) would answer.

A refusal that withholds its reason is worse than no check at all, so it is pinned here.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_authoring.domain.finding import ERROR, Finding
from agent_authoring.domain.report import Report
from agent_authoring.presentation.publish_agent_tool import PublishAgentTool

BROKEN = Report(
    agent_id="demo",
    findings=(
        Finding(
            level=ERROR,
            code="ORPHANED_UI",
            message="ui/ exists but agent.toml has NO [app] section",
            path="agent.toml",
            fix="restore the [app] table",
        ),
    ),
)


class StubValidator:
    def __init__(self, report):
        self._report = report

    def validate(self, agent_id):  # noqa: ARG002 — one canned answer is the point
        return self._report


class StubRegistry:
    def __init__(self, agents_dir):
        self.agents_dir = str(agents_dir)


def _agent(tmp_path, agent_id="demo"):
    d = tmp_path / agent_id
    d.mkdir()
    (d / "agent.toml").write_text('name = "Demo"\n', encoding="utf-8")
    return d


def _run(tool, **params):
    return asyncio.run(tool.execute("call-1", {"agent_id": "demo", **params}, abort=None))


def test_a_refusal_carries_the_whole_report_not_just_the_verdict(tmp_path):
    _agent(tmp_path)
    tool = PublishAgentTool(object(), StubRegistry(tmp_path), StubValidator(BROKEN))

    result = _run(tool)

    assert result.is_error
    text = result.content[0].text
    # the verdict line, AND every part of the finding the author needs to act on
    assert "refusing to publish" in text
    assert "FAILED (1 error)" in text
    assert "agent.toml" in text
    assert "NO [app] section" in text
    assert "restore the [app] table" in text


def test_a_clean_report_does_not_stop_the_publish(tmp_path):
    """It must fall through to the target check, not to the validation refusal — otherwise a
    passing agent could still be blocked by a gate that mis-reads `ok`."""
    _agent(tmp_path)
    tool = PublishAgentTool(object(), StubRegistry(tmp_path), StubValidator(Report("demo")))

    result = _run(tool, target="")

    assert "refusing to publish: validate_agent" not in result.content[0].text


# ─────────────────────────── visible is not publishable ───────────────────────────
#
# On a layered registry (hosted, or desktop signed-in) the deployment's catalogue is in
# everyone's list, and publishing one of those agents would re-sign someone else's work under
# the caller's creator identity. The registry answers two questions publish used to conflate:
# resolve_dir (where IS it — the read union) and owns (whose is it — the write layer).


class LayeredRegistry:
    """The three answers a layered registry gives; agents_dir stays the write target."""

    def __init__(self, agents_dir, dirs, owned, origins=None):
        self.agents_dir = str(agents_dir)
        self._dirs = dirs
        self._owned = owned
        self._origins = origins or {}

    def resolve_dir(self, agent_id):
        return self._dirs.get(agent_id)

    def owns(self, agent_id):
        return agent_id in self._owned

    def origin_of(self, agent_id):
        return self._origins.get(agent_id, "authored")


def test_a_catalogue_agent_is_found_but_refused(tmp_path):
    """THE regression, both halves. Before resolve_dir the lookup failed with a path error naming
    the caller's (empty) overlay; with it the agent is FOUND — and then refused for the real
    reason: it is not theirs. The validator must not even be consulted — findings about someone
    else's agent are noise on top of a refusal that is already final."""
    d = _agent(tmp_path)
    registry = LayeredRegistry(tmp_path / "overlay", {"demo": d}, owned=set())
    tool = PublishAgentTool(object(), registry, StubValidator(BROKEN))

    result = _run(tool)

    assert result.is_error
    text = result.content[0].text
    assert "not yours to publish" in text
    assert "Nothing was built or sent" in text
    assert "refusing to publish: validate_agent" not in text, "ownership is refused before validation"


def test_an_owned_agent_publishes_from_wherever_it_lives(tmp_path):
    """An owned agent resolved OUTSIDE agents_dir (the union found it in another layer) must fall
    through ownership to the ordinary target check — location is no longer part of the answer."""
    d = _agent(tmp_path)
    registry = LayeredRegistry(tmp_path / "overlay", {"demo": d}, owned={"demo"})
    tool = PublishAgentTool(object(), registry, StubValidator(Report("demo")))

    result = _run(tool, target="")

    text = result.content[0].text
    assert "not yours to publish" not in text
    assert "no agent 'demo'" not in text


def test_an_installed_agent_is_owned_but_not_publishable(tmp_path):
    """Owned ≠ authored. A marketplace install is the caller's to run and uninstall, but its
    author is the only one who can ship a new version — republishing would re-sign their work
    under the caller's creator identity."""
    d = _agent(tmp_path)
    registry = LayeredRegistry(
        tmp_path / "overlay", {"demo": d}, owned={"demo"}, origins={"demo": "installed"}
    )
    tool = PublishAgentTool(object(), registry, StubValidator(Report("demo")))

    result = _run(tool)

    assert result.is_error
    text = result.content[0].text
    assert "from the marketplace" in text
    assert "Nothing was built or sent" in text


def test_a_registry_without_layers_owns_everything(tmp_path):
    """The single-user registry (plain agents_dir, no resolve_dir/owns) must behave exactly as it
    always has — the gate degrades to open, never to shut."""
    _agent(tmp_path)
    tool = PublishAgentTool(object(), StubRegistry(tmp_path), StubValidator(Report("demo")))

    result = _run(tool, target="")

    assert "not yours to publish" not in result.content[0].text
