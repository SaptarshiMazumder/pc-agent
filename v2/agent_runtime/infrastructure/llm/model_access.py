"""FunnelModelAccess — the default ModelAccess: a thin delegator onto the oneshot funnel.

Every guarantee (mode-correct transport, proxy metering, BYOK fallback, sandbox brokering)
lives in ``oneshot``/``model_proxy``; this class only gives it the port's SHAPE so tools hold a
``self.models`` handle instead of importing a module. Deliberately stateless and import-light.

THE IMPORTS ARE DEFERRED AND MUST STAY THAT WAY: inside the plugin sandbox the child pre-seeds
``sys.modules`` with a broker-backed ``oneshot`` stub (sandbox/child_models.py) BEFORE any plugin
code runs. Because each method imports at call time, the same ``self.models.vision(...)`` a tool
wrote on the author's desktop is transparently served by the host when the tool runs untrusted —
no second code path, which is the whole point.
"""

from __future__ import annotations


class FunnelModelAccess:
    """See module docstring. Fake it in tests by assigning ``tool._model_access``."""

    def text(self, *, model, prompt, max_tokens=None, api_key=None, timeout=None) -> str:
        from agent_runtime.infrastructure.llm.oneshot import text_complete

        return text_complete(
            model=model, prompt=prompt, max_tokens=max_tokens, api_key=api_key, timeout=timeout
        )

    def vision(
        self, *, model, prompt, image_paths, want_json=False, api_key=None, timeout=None
    ) -> str:
        from agent_runtime.infrastructure.llm.oneshot import vision_complete

        return vision_complete(
            model=model,
            prompt=prompt,
            image_paths=image_paths,
            want_json=want_json,
            api_key=api_key,
            timeout=timeout,
        )

    def generate_image(
        self,
        *,
        model,
        prompt,
        out_path,
        reference_images=None,
        aspect_ratio=None,
        image_size=None,
        api_key=None,
        timeout=None,
    ) -> dict:
        from agent_runtime.infrastructure.llm.oneshot import generate_image

        return generate_image(
            model=model,
            prompt=prompt,
            out_path=out_path,
            reference_images=reference_images,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            api_key=api_key,
            timeout=timeout,
        )


_default: FunnelModelAccess | None = None


def default_model_access() -> FunnelModelAccess:
    """The process-wide default handle. Stateless, so one instance serves every tool; the
    per-turn mode/billing decision happens inside the funnel (model_proxy contextvars), which is
    exactly what keeps the daemon stateless."""
    global _default
    if _default is None:
        _default = FunnelModelAccess()
    return _default
