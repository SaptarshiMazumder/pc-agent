"""Cost-efficiency brain ROUTING — pick the reasoning (brain) model per iteration by NEED.

A decoupled seam over the agent loop (sibling to context_policy / observers / failover):
default OFF => no router => the agent's normal brain runs every turn, exactly as before.

When ``config.cost_efficiency.enabled`` is set, the loop asks the router which model to use for THIS
inference. The rule is deliberately narrow and correct: use a cheap ``text_model`` for ordinary turns,
and escalate to a multimodal ``vision_model`` ONLY on iterations whose OUTGOING context actually
carries an image the brain must SEE (an ImageContent block anywhere in the messages).

Why that exact trigger: a text-only brain (e.g. DeepSeek) can still DECIDE to generate an image — that
is a tool call it emits with text args, and the imagegen tool renders the pixels with its OWN model.
The brain never touches those pixels. The ONLY thing a text model physically cannot do is READ an image
sitting in its context (litellm forwards tool-result images so "a vision-capable model can SEE them" —
a text model would choke on / ignore them). So that — and only that — is when we spend the vision model.

Emergent (and safe) behavior: once an image enters the history it stays there every iteration (the
context is re-sent each turn), so routing naturally becomes "cheap text brain until the first image
enters context, then the vision brain onward" — which is correct, because that history can never be
handed to the text-only model again anyway.

CONFIG-ONLY (models come from config, never env), matching the rest of the models layer.
"""

from __future__ import annotations

from agentd.domain.messages import ImageContent


def _has_image(messages) -> bool:
    """True if any message carries an ImageContent block — i.e. the brain must SEE something this
    turn. UserMessage.content is a plain str (no blocks); images ride on Assistant / ToolResult
    messages, whose ``content`` is a list of blocks. Defensive: tolerate either shape."""
    for m in messages or []:
        content = getattr(m, "content", None)
        if isinstance(content, list):
            for b in content:
                if isinstance(b, ImageContent):
                    return True
    return False


class CostEfficiencyRouter:
    """Route each inference by need: ``text_model`` for text-only turns, ``vision_model`` when the
    context has an image to read. An UNSET side falls back to the loop's default brain, so setting
    only ``text_model`` still works (image turns then keep the agent's normal multimodal brain), and
    setting only ``vision_model`` just escalates image turns while text turns keep the normal brain."""

    def __init__(self, text_model: str | None, vision_model: str | None):
        self.text_model = text_model or None
        self.vision_model = vision_model or None

    def __call__(self, default_model: str, messages) -> str:
        if _has_image(messages):
            return self.vision_model or default_model
        return self.text_model or default_model


def build_model_router(config):
    """Build the cost-efficiency router from config, or None (=> unchanged: one brain every turn).

    Reads ``config.cost_efficiency = {enabled, text_model, vision_model}``. Returns None when the
    block is absent, disabled, or names no models — so the default is always "no routing"."""
    ce = getattr(config, "cost_efficiency", None) or {}
    if not isinstance(ce, dict) or not ce.get("enabled"):
        return None
    text_model = ce.get("text_model")
    vision_model = ce.get("vision_model")
    if not text_model and not vision_model:
        return None
    return CostEfficiencyRouter(text_model, vision_model)
