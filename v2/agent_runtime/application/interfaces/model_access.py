"""ModelAccess — the ONE port through which a tool touches a model provider.

The contract, in one sentence: a tool asks for model WORK; the runtime owns the transport, the
credential and the billing. A tool never imports a provider SDK, never reads an env key, and
never branches on the deployment mode — the same tool code is correct in local BYOK (direct on
the user's own keys), desktop cloud and web hosted (routed through the platform's LiteLLM proxy
and metered per account), and inside the plugin sandbox (served by the host via the model
broker, granted and clamped).

Tools reach it as ``self.models`` (see ``Tool.models``): declare ``needs_model = True`` +
``model_kind``, resolve the id with ``self.resolve_model(config)``, and call the shape you need.
The default implementation is a thin delegator onto the ``oneshot`` funnel, where every one of
those guarantees actually lives — this Protocol exists so the engine and tests depend on a
SHAPE (fake it by assigning ``tool._model_access``), never on the funnel module itself.

Adding a model shape (audio, video, …) = one method here, one function in ``oneshot``, one
request kind in the sandbox model broker + child stub. Never a provider client in a plugin.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ModelAccess(Protocol):
    """The model shapes the platform offers a tool. All synchronous — tools already run their
    work via ``asyncio.to_thread``. ``api_key`` is a per-call BYOK override everywhere; proxied
    turns ignore it (the turn's own credential pays), and the sandbox ignores it entirely."""

    def text(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """One text prompt -> the model's text."""
        ...

    def vision(
        self,
        *,
        model: str,
        prompt: str,
        image_paths,
        want_json: bool = False,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """One prompt + N images -> the model's text (or JSON string in JSON mode)."""
        ...

    def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        out_path,
        reference_images=None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> dict:
        """One prompt (+ reference conditioning) -> ONE image written to ``out_path``.
        Returns ``{path, mime, model}``."""
        ...
