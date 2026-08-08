"""UiTemplate — what a startable agent app is made of.

A registry, not a factory: every entry names files that already exist on disk, in this agent's
own ``skills/build-agent/templates/``. Nothing here reads or writes anything — the copying is
``ScaffoldUiService``'s job — so the catalogue can be listed, tested and reasoned about with no
filesystem at all.

Two provenances, and the distinction is the point:

  ``files``    the template's OWN files. Copied from ``templates/<id>/``. These are what the
               model then edits: markup, styling, the agent's surface.

  ``borrowed`` files taken from Agent Builder's LIVE ``ui/`` instead of from the template.
               ``vendor/agentd-client.js`` is the SDK and ``md.js`` is the renderer; keeping a
               second copy under ``templates/`` would mean two versions of each in one product,
               and the SDK copy could then disagree with the daemon it talks to. One source,
               copied at scaffold time, cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UiTemplate:
    id: str
    title: str
    summary: str
    files: tuple[str, ...]      # relative to templates/<id>/
    borrowed: tuple[str, ...]   # relative to agent-builder's own ui/
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


class UiTemplates:
    """The catalogue. Frozen at one entry ON PURPOSE.

    A picker with a single option is ceremony, and a second template written before the first
    has been used in anger would be two guesses instead of one. Add the next one when a real
    agent wants a shape this cannot make — and when it is added, it must ship with the same
    tests, or it becomes the untested path everyone picks.
    """

    def __init__(self, templates: tuple[UiTemplate, ...] = (CHAT_APP,)):
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
