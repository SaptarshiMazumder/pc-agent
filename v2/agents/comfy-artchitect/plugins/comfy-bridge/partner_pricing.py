"""What a workflow will cost before it runs — read from partner-nodes.json, never guessed.

WHY THIS EXISTS. ComfyUI's partner nodes bill the PUBLISHER's prepaid balance, not the user's.
Comfy publishes prices but exposes no balance or usage API, so there is no way to learn what a
run cost after the fact: the only moment the cost is knowable is before submitting, from the
graph itself. That single fact shapes everything here.

REFUSING IS THE DEFAULT, AND IT IS THE WHOLE SAFETY PROPERTY. A partner node this table does not
price would otherwise run for free on the publisher's balance, and the first sign of it would be
the invoice. So a node recognised as billable but not priced makes `price_workflow` return a
quote that is NOT ok, naming the class — a two-minute edit to the JSON rather than a silent leak.

RECOGNITION IS BY PROVIDER PREFIX, NOT EXACT CLASS NAME. Comfy adds and renames nodes faster than
any table can track, and an exact-match table fails OPEN — a renamed Kling node stops matching
and silently becomes free. A prefix ("Kling") keeps matching across renames, and the model is
then found by searching the node's own inputs.

BUT THE PREFIX ONLY SAYS WHICH PROVIDER, NEVER WHETHER A NODE IS PAID AT ALL. That is the
instance's call: ComfyUI marks every partner node `api_node: true` in its own schema, and the
core nodes it ships for free do not carry the flag. A prefix cannot tell them apart —
`FluxKontextMultiReferenceLatentMethod` is a free conditioning node that happens to start with
"Flux", and matched here it priced a local Qwen graph as a $7.72 BFL video job, which the credit
gate then refused, three chats in a row. So the caller asks the instance and passes
`free_classes`; a class the instance says is free is never priced, whatever it is called. With
no instance to ask the prefix is all there is, and it errs toward charging — the direction this
must err.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: The table, BESIDE THIS FILE rather than at the agent root, and that is not a filing
#: preference — it is what makes it readable at all.
#:
#: An installed agent's plugins are SANDBOXED, and the sandbox copies the plugin's own directory
#: to a guest path (/tmp/exec-<id>/...). Resolving upward from __file__ therefore lands outside
#: anything the grant covers, and the first real run on staging proved it:
#:
#:   SandboxDenied: not allowed to read that path (/tmp/exec-b6a2w56c/partner-nodes.json)
#:
#: `__file__.parent` is the one location guaranteed to exist wherever this code was copied to.
#: Same class of bug as the guest/host path confusion in comfy_download and comfy_emit: a path
#: that means one thing on the author's machine and another inside the sandbox.
_TABLE = Path(__file__).resolve().parent / "partner-nodes.json"

#: Input names that plausibly carry a duration, in the order we trust them.
_DURATION_KEYS = ("duration", "duration_seconds", "seconds", "length", "video_length", "n_seconds")

#: Input names that plausibly carry a resolution/quality tier.
_TIER_KEYS = ("resolution", "mode", "quality", "size", "aspect_ratio", "profile")


@dataclass
class LineItem:
    node_id: str
    class_type: str
    provider: str
    model: str
    unit: str
    quantity: float
    rate: float
    credits: float
    note: str = ""


@dataclass
class Quote:
    """What a graph costs, and whether we are willing to run it."""

    items: list[LineItem] = field(default_factory=list)
    unpriced: list[str] = field(default_factory=list)
    markup: float = 1.0
    #: How the TABLE denominates a credit (Comfy's: 100 per dollar). Not the platform's.
    credits_per_usd: float = 100.0

    @property
    def ok(self) -> bool:
        """False when ANY billable node could not be priced — see the module docstring."""
        return not self.unpriced

    @property
    def raw_credits(self) -> float:
        return sum(i.credits for i in self.items)

    @property
    def credits(self) -> int:
        """What we charge, rounded UP: a fractional credit that rounds down is a rounding error
        that always favours the user and always costs the publisher."""
        import math

        return int(math.ceil(self.raw_credits * self.markup))

    @property
    def paid(self) -> bool:
        return bool(self.items)

    @property
    def usd(self) -> float:
        """DOLLARS OF PROVIDER COST, drift insurance included — the one number that means the
        same thing to Comfy, to the platform's ledger and to a person. The table's credits are
        Comfy's; the platform's credits are its own; dollars are the bridge between them."""
        return self.raw_credits * self.markup / self.credits_per_usd if self.credits_per_usd else 0.0

    def platform_credits(self, rate: float) -> int:
        """What the platform charges: dollars x its own credits-per-dollar, rounded UP."""
        import math

        return int(math.ceil(self.usd * float(rate))) if rate else 0

    def as_text(self, platform_rate: float | None = None) -> str:
        if not self.items and not self.unpriced:
            return "no paid services — this workflow runs entirely on the instance's own GPU."
        lines = []
        per = self.credits_per_usd or 100.0
        for i in self.items:
            qty = f"{i.quantity:g}×" if i.unit != "per_second" else f"{i.quantity:g}s"
            lines.append(f"  {i.model} ({i.provider}) — {qty} @ ${i.rate / per:.3f}/unit = "
                         f"${i.credits / per:.2f}")
        if self.items:
            total = f"  TOTAL ≈ ${self.usd:.2f} (incl. {self.markup:g}x)"
            if platform_rate:
                total += f" → {self.platform_credits(platform_rate):,} credits"
            lines.append(total)
        for cls in self.unpriced:
            lines.append(f"  !! {cls} is a paid node with NO PRICE in partner-nodes.json — "
                         "it cannot run until someone adds it")
        return "\n".join(lines)


def load_table(path: Path | None = None) -> dict:
    return json.loads((path or _TABLE).read_text(encoding="utf-8"))


def _provider_for(class_type: str, table: dict) -> dict | None:
    name = (class_type or "").lower()
    for provider in table.get("providers") or []:
        for prefix in provider.get("match") or []:
            if str(prefix).lower() in name:
                return provider
        # A REGEX for the providers a substring cannot express: the hosted Wan nodes share their
        # prefix with ComfyUI's free local Wan nodes and differ only in ending with "Api".
        for pattern in provider.get("match_regex") or []:
            try:
                if re.search(str(pattern), name):
                    return provider
            except re.error:
                continue
    return None


def _strings_in(inputs: dict) -> list[str]:
    """Every literal string in a node's inputs, lowercased — where a model name will be."""
    out = []
    for value in (inputs or {}).values():
        if isinstance(value, str):
            out.append(value.lower())
    return out


def _model_for(provider: dict, inputs: dict, class_type: str = "") -> str:
    """Which of the provider's models this node names, else the provider's default.

    THE CLASS FIRST, for models that are their node: BFL's nodes, Kling's lip-sync — nothing in
    their inputs says which model, the class name does (`classes` on the model entry). Then the
    inputs, longest name first, so "kling-v3-omni" is not swallowed by "kling-v3".
    """
    models = provider.get("models") or {}
    cls = (class_type or "").lower()
    if cls:
        for model, rates in models.items():
            for needle in (rates.get("classes") or []) if isinstance(rates, dict) else []:
                if str(needle).lower() in cls:
                    return model
    # THE INPUTS, NORMALISED ON BOTH SIDES. The node says "seedream 5.0 pro"; the table says
    # "seedream-5-0-pro". Dots, spaces and case are not a different model — and when this
    # comparison was raw, twelve Seedream image nodes priced as twelve Seedance videos.
    haystack = " " + " ".join(_norm(s) for s in _strings_in(inputs)) + " "
    for model in sorted(models, key=len, reverse=True):
        if _norm(model) and _norm(model) in haystack:
            return model
    # THE CLASS'S FAMILY, WORST CASE. A Seedream node whose variant the table cannot place is
    # still a Seedream node: the dearest Seedream rate, never the provider's default, which for
    # ByteDance is a video model at fifty times the price.
    family_rates = {}
    for model, rates in models.items():
        family = _norm(model).split("-")[0]
        if family and family in cls:
            family_rates[model] = _peak(rates)
    if family_rates:
        return max(family_rates, key=family_rates.get)
    return str(provider.get("default_model") or "")


def _norm(value) -> str:
    """One spelling for a model name: lowercase, every run of non-alphanumerics a single dash."""
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def _peak(rates) -> float:
    """The highest rate a model entry carries — the honest guess when its variant is unknown."""
    if not isinstance(rates, dict):
        return 0.0
    tiers = [float(v) for k, v in rates.items() if not str(k).startswith("_") and k != "classes" and isinstance(v, (int, float))]
    return max(tiers) if tiers else float(rates.get("_default") or 0.0)


def _tier_for(rates: dict, inputs: dict) -> tuple[str, float]:
    """The rate for this node's resolution/quality, else the model's `_default`."""
    # Rate tiers only: `_default` is the fallback, `_unit` and `classes` are metadata.
    named = {k: v for k, v in rates.items() if not str(k).startswith("_") and k != "classes"}
    if named:
        blob = " ".join(_strings_in(inputs))
        for field_name in _TIER_KEYS:
            value = (inputs or {}).get(field_name)
            if isinstance(value, str):
                blob += " " + value.lower()
        for tier in sorted(named, key=len, reverse=True):
            if tier.lower() in blob:
                return tier, float(named[tier])
    return "_default", float(rates.get("_default") or (max(named.values()) if named else 0.0))


def _seconds_for(provider: dict, inputs: dict) -> float:
    for key in _DURATION_KEYS:
        value = (inputs or {}).get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        if isinstance(value, str):
            digits = "".join(ch for ch in value if ch.isdigit() or ch == ".")
            if digits:
                try:
                    seconds = float(digits)
                except ValueError:
                    continue
                if seconds > 0:
                    return seconds
    # NO DURATION FOUND MEANS THE PROVIDER'S DEFAULT, NOT ZERO. A zero would price a video at
    # nothing, which is the one direction this must never round.
    return float(provider.get("default_seconds") or 5)


def price_workflow(
    api_graph: dict, table: dict | None = None, free_classes: set[str] | None = None
) -> Quote:
    """Price an emitted `.api.json` graph — the same shape comfy_run submits.

    `free_classes`: classes the INSTANCE reports as not `api_node` — never priced, whatever the
    table's prefixes would make of their names (see the module docstring)."""
    data = table or load_table()
    free = set(free_classes or ())
    quote = Quote(
        markup=float(data.get("markup") or 1.0),
        credits_per_usd=float(data.get("credits_per_usd") or 100.0),
    )

    for node_id, node in (api_graph or {}).items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        if class_type in free:
            continue  # the instance says so; a prefix match is a name, not a bill
        provider = _provider_for(class_type, data)
        if provider is None:
            continue  # a local node: it costs GPU time, which is the rental, not credits

        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        model = _model_for(provider, inputs, class_type)
        rates = (provider.get("models") or {}).get(model)
        if not rates:
            quote.unpriced.append(class_type)
            continue

        tier, rate = _tier_for(rates, inputs)
        unit = str(rates.get("_unit") or provider.get("unit") or "per_run")
        quantity = _seconds_for(provider, inputs) if unit == "per_second" else 1.0
        quote.items.append(
            LineItem(
                node_id=str(node_id),
                class_type=class_type,
                provider=str(provider.get("label") or provider.get("id") or ""),
                model=model,
                unit=unit,
                quantity=quantity,
                rate=rate,
                credits=rate * quantity,
                note="" if tier == "_default" else tier,
            )
        )
    return quote
