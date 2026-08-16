"""DocumentEmbedder — vectors for chunks, and an honest answer about whether there are any.

THE ONE THING THIS CLASS EXISTS TO PREVENT. Semantic search is not always available: it needs an
embedding provider and a working key. The platform's own contract for this is fail-open —
`infrastructure/embeddings.py` says callers treat a missing embedder as "no vectors" and fall back
to keyword search, because semantic recall is an enhancement and must never break a turn.

Falling back is correct. Falling back SILENTLY is not. A lexical answer presented as a semantic one
teaches the user that the agent "doesn't understand synonyms", when the truth is that nobody
configured a key. So `available` and `unavailable_reason` are part of this object's surface, the
search tool prints the mode it used, and the reason is a real message rather than a shrug.

This is the narrow, sanctioned exception to "let errors surface": there is a genuine alternate
path, it is taken deliberately, and it is named in the output every single time.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")

#: Matches the platform's memory default. Any litellm embedding model works —
#: `text-embedding-3-small` (OpenAI) or a local `ollama/nomic-embed-text` (no key at all).
DEFAULT_EMBED_MODEL = "gemini/text-embedding-004"


class DocumentEmbedder:
    """Wraps the platform embedder. Never constructs a network client until asked to embed."""

    def __init__(self, config=None):
        self._config = config
        self._model = ""
        self._embed = None
        self._reason = ""
        self._resolve()

    def _resolve(self) -> None:
        try:
            from agent_runtime.application.tool_models import resolve_tool_model
            from agent_runtime.infrastructure.embeddings import build_embed_fn
        except ImportError as e:  # pragma: no cover — the runtime is always present in practice
            self._reason = f"the runtime's embedding helpers are unavailable ({e})"
            return

        self._model = resolve_tool_model(
            self._config, "paper-library", "embed", default=DEFAULT_EMBED_MODEL
        ) or ""
        if not self._model:
            self._reason = "no embedding model is configured (plugins.paper-library.tools.embed)"
            return
        self._embed = build_embed_fn(self._model)
        if self._embed is None:
            self._reason = f"the embedding model {self._model!r} produced no embedder"

    @property
    def model(self) -> str:
        return self._model

    @property
    def available(self) -> bool:
        return self._embed is not None

    @property
    def unavailable_reason(self) -> str:
        return self._reason or "no embedder"

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Vectors for these texts.

        Raises when an embedder EXISTS but the call fails — a key that stopped working is a real
        failure the user can fix, and swallowing it would leave chunks silently unembedded
        forever. Absence of an embedder is the alternate path; failure of one is not.

        A failure also RETIRES this embedder for the rest of the run. `build_embed_fn` hands back a
        callable whenever a model name is configured — it cannot know whether a key exists — so on
        a machine with no key the first call fails and every later one would fail identically.
        Ingesting forty documents would mean forty network timeouts to learn the same fact once.
        The error is not swallowed: it becomes `unavailable_reason`, so every subsequent result
        still says exactly why search is lexical.
        """
        if self._embed is None:
            raise RuntimeError(f"cannot embed: {self.unavailable_reason}")
        try:
            return self._embed(texts)
        except Exception as e:
            self._embed = None
            self._reason = f"the embedding model {self._model!r} failed: {type(e).__name__}: {e}"
            log.warning("paper-library: embeddings retired for this run — %s", self._reason)
            raise
