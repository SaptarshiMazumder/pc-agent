"""UiTemplate — what a startable agent app is made of.

A registry, not a factory: every entry names files that already exist on disk, in this agent's
own ``skills/build-agent/templates/``. Nothing here reads or writes anything — the copying is
``ScaffoldUiService``'s job — so the catalogue can be listed, tested and reasoned about with no
filesystem at all.

Two provenances, and the distinction is the point:

  ``files``    the template's OWN files. Copied from ``templates/<id>/``. These are what the
               model then edits: markup, styling, the agent's surface.

  ``borrowed`` files taken from ``templates/_borrowed/`` — shared by every template instead of
               being copied into each one. ``vendor/agentd-client.js`` is the SDK and ``md.js``
               is the renderer; a per-template copy would mean three versions of each in one
               product, and the SDK copies could then disagree with the daemon they talk to. One
               source, copied at scaffold time, cannot drift.

               That source used to be Agent Builder's own live ``ui/``. It moved when that folder
               became a Vite build output, because a build empties its output directory — the one
               copy every agent is scaffolded from is not something to leave in the path of
               ``npm run build``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UiTemplate:
    id: str
    title: str
    summary: str
    files: tuple[str, ...]      # relative to templates/<id>/
    borrowed: tuple[str, ...]   # relative to templates/_borrowed/
    readme: str                 # which of `files` is the instructions the model must read

    @property
    def all_files(self) -> tuple[str, ...]:
        """Every path this template produces under the target agent's ``ui/``."""
        return tuple(sorted(set(self.files) | set(self.borrowed)))


CHAT_APP = UiTemplate(
    id="chat-app",
    title="Chat app",
    summary=(
        "A full conversation window: streamed replies, live tool rows, image paste/drop, "
        "saved conversation history, and a settings page where the user pastes their own "
        "API key (BYOK). The default — start here unless the agent needs no conversation."
    ),
    files=(
        "index.html",
        "app.js",
        "chat.js",
        "settings.js",
        "style.css",
        "README.md",
    ),
    borrowed=(
        "md.js",
        "vendor/agentd-client.js",
    ),
    readme="README.md",
)


DASHBOARD_APP = UiTemplate(
    id="dashboard-app",
    title="Dashboard",
    summary=(
        "Numbers, a chart and a table, on screen the moment the window opens — driven by direct "
        "tool calls, so Refresh costs no model turn. Keeps a chat view for the questions a number "
        "cannot answer. For an agent that runs on its own and REPORTS."
    ),
    files=(
        "index.html",
        "app.js",
        "board.js",
        "chat.js",
        "settings.js",
        "style.css",
        "README.md",
    ),
    borrowed=(
        "md.js",
        "vendor/agentd-client.js",
    ),
    readme="README.md",
)


WORKBENCH_APP = UiTemplate(
    id="workbench-app",
    title="Workbench",
    summary=(
        "A drop zone and a queue with per-item status: drop a pile of files, watch each one go "
        "through, see which failed and why. One failure never stops the batch. For an agent that "
        "INGESTS things rather than discussing them."
    ),
    files=(
        "index.html",
        "app.js",
        "queue.js",
        "chat.js",
        "settings.js",
        "style.css",
        "README.md",
    ),
    borrowed=(
        "md.js",
        "vendor/agentd-client.js",
    ),
    readme="README.md",
)


class UiTemplates:
    """The catalogue.

    THREE SHAPES, because the shape is the decision — and nothing used to tell the model that
    the decision existed. A trading monitor whose window is a chat box makes the user type
    "what's my P&L" to see a number that should already be on screen; a file-ingest agent whose
    window is a chat box makes them describe files they could have dropped. Both were the only
    thing this catalogue could produce.

    ``chat-app`` stays FIRST and therefore the default: it is right whenever the work genuinely
    is a conversation, which is still most agents.

    Every entry is held to the same tests — on disk, no validator findings, parses, no id
    reached for that the markup does not declare. An untested template is worse than a missing
    one, because it is the path everyone picks.
    """

    def __init__(
        self, templates: tuple[UiTemplate, ...] = (CHAT_APP, DASHBOARD_APP, WORKBENCH_APP)
    ):
        self._by_id = {t.id: t for t in templates}
        self._default = templates[0].id if templates else ""

    @property
    def default_id(self) -> str:
        return self._default

    def ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    def get(self, template_id: str) -> UiTemplate | None:
        return self._by_id.get((template_id or self._default).strip())

    def describe(self) -> str:
        return "\n".join(f"  {t.id} — {t.summary}" for t in self._by_id.values())
