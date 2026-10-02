"""FamilyRuleValidator — is this workflow right for the models it runs? Layer 2 of 3.

GraphStructuralValidator says a graph will be ACCEPTED by ComfyUI. This says whether it will be
any GOOD: the knowledge base's rules for each family the graph runs — the VAE a model decodes with,
the cfg a distilled LoRA needs, the frame arithmetic of a video model, a sampler handover between
two experts. Those are the mistakes that render, cost a GPU's time, and come back as mush.

WHICH RULES: those of every family whose model files the graph names (KnowledgeBaseCatalog). A
rule is one of

    pair        when some node matches `when`, some node (or `all` of them) must match `require`
    numeric     a value on matching nodes must satisfy min / max / in / multiple_of / modulo
    upstream    following input `via` upstream from a `when` node must reach a `require` node
    version     the recipe (or the rule) needs at least this ComfyUI
    structural  a named graph walk (FamilyStructuralChecks)

and each carries a severity: `error` blocks the design; `warn` must be answered — fixed, or said
why it is right here.

A VALUE THAT IS NOT IN THE GRAPH IS NOT JUDGED: an input wired to something that computes it (a
GetImageSize, a math node) is unknown until it runs. Such a rule is counted as not judged, never as
passed, and never as failed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from api_graph import ApiGraph
from family_profile import FamilyProfile
from family_structural_checks import FamilyStructuralChecks
from knowledge_base_catalog import KnowledgeBaseCatalog
from recipe import Recipe, version_tuple

ERROR = "error"
WARN = "warn"

_UNKNOWN = object()  # a value the graph does not hold (computed at run time)
NEVER = "__never__"
#: The structural check that judges a prompt against the family's own format.
PROMPT_FORMAT_CHECK = "prompt_has_sections"


@dataclass(frozen=True)
class RuleFinding:
    severity: str
    family: str
    rule: str
    message: str
    where: str = ""

    def render(self) -> str:
        at = f" [{self.where}]" if self.where else ""
        return f"{self.family}/{self.rule}{at}: {self.message}"


@dataclass
class RuleReport:
    families: list[str]
    findings: list[RuleFinding]
    checked: int
    not_judged: list[str]  # "<family>/<rule>": the graph alone could not say
    #: family id -> its prompting guide, for each family whose prompt-format check failed: the
    #: format is the fix, so it travels with the finding rather than behind a file to open.
    prompt_guides: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.prompt_guides is None:
            self.prompt_guides = {}

    @property
    def errors(self) -> list[RuleFinding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[RuleFinding]:
        return [f for f in self.findings if f.severity != ERROR]


class FamilyRuleValidator:
    def __init__(self, catalog: KnowledgeBaseCatalog, checks: FamilyStructuralChecks) -> None:
        self._catalog = catalog
        self._checks = checks

    def check(self, graph: dict, comfyui_version: str = "", recipe: Recipe | None = None,
              vram_gb: float = 0.0, gpu_name: str = "") -> RuleReport:
        g = ApiGraph(graph)
        families = self._catalog.families_for(graph)
        if recipe is not None and recipe.family in self._catalog.families and \
                self._catalog.families[recipe.family] not in families:
            families.append(self._catalog.families[recipe.family])
        report = RuleReport([f.id for f in families], [], 0, [])
        for fam in families:
            for rule in fam.rules:
                # MACHINE ADVICE ("fp8 is only faster on Ada and newer") is about the box, not the
                # graph: judged only once the box is known, and skipped on the cards it exempts.
                if rule.get("gpu_unless"):
                    if not gpu_name:
                        report.not_judged.append(f"{fam.id}/{rule.get('id')}")
                        continue
                    if re.search(str(rule["gpu_unless"]), gpu_name, re.I):
                        continue
                # A GENERAL RULE A SPECIFIC FILE OVERRIDES ("trained at these sizes" vs a LoRA trained at
                # 2:1): the profile names the files that lift it, and the file's own rules apply instead.
                if rule.get("unless_file") and any(re.search(str(rule["unless_file"]), s, re.I) for s in g.strings()):
                    continue
                self._apply(fam, rule, g, comfyui_version, recipe, report)
            if vram_gb > 0:
                report.findings += self._machine_fit(fam, g, vram_gb)
        return report

    # ------------------------------------------------------------------ dispatch

    def _apply(self, fam: FamilyProfile, rule: dict, g: ApiGraph, version: str,
               recipe: Recipe | None, report: RuleReport) -> None:
        kind, rid = rule.get("kind"), str(rule.get("id") or "?")
        severity = ERROR if rule.get("severity") == ERROR else WARN
        message = str(rule.get("message") or "")
        report.checked += 1
        if kind == "structural":
            try:
                problems = self._checks.run(str(rule.get("structural")), g, rule.get("args") or {}, fam)
            except KeyError:
                report.findings.append(RuleFinding(WARN, fam.id, rid, (
                    f"NOT CHECKED: this agent has no check named '{rule.get('structural')}' — the "
                    "knowledge base is newer than the code. Judge it by hand: " + message)))
                return
            if problems is None:
                report.not_judged.append(f"{fam.id}/{rid}")
            for p in problems or []:
                report.findings.append(RuleFinding(severity, fam.id, rid, f"{p}. {message}"))
            if problems and rule.get("structural") == PROMPT_FORMAT_CHECK:
                report.prompt_guides[fam.id] = fam.prompting_guide()
            return
        if kind == "version":
            self._version(fam, rule, rid, severity, message, version, recipe, report)
            return
        handler = {"pair": self._pair, "numeric": self._numeric, "upstream": self._upstream}.get(kind)
        if handler is None:
            report.findings.append(RuleFinding(WARN, fam.id, rid, f"NOT CHECKED: unknown rule kind '{kind}'"))
            return
        handler(fam, rule, rid, severity, message, g, report)

    # ------------------------------------------------------------------ pair / upstream

    def _pair(self, fam, rule, rid, severity, message, g: ApiGraph, report: RuleReport) -> None:
        when, require = rule.get("when") or {}, rule.get("require") or {}
        triggers = [nid for nid in g.of_class(when.get("node", "")) if _holds(g, nid, when, any_input=True) is True]
        if not triggers:
            return
        candidates = g.of_class(require.get("node", ""))
        # `"equals": "__never__"` is the profiles' way to say "this node must not be in the graph
        # at all" (a Wan 2.2 I2V with CLIP vision, a FastH3 graph with a reference node).
        if require.get("equals") == NEVER:
            for nid in candidates:
                report.findings.append(RuleFinding(severity, fam.id, rid, message,
                                                   f"node {nid} {g.class_of(nid)}"))
            return
        # A multi-input requirement (`cfg|video_cfg`) holds on a node when ANY of the inputs it has
        # holds — or, with `all`, when EVERY input it has holds.
        verdicts = {nid: _holds(g, nid, require, any_input=not require.get("all")) for nid in candidates}
        if require.get("all"):
            bad = [nid for nid, ok in verdicts.items() if ok is False]
            for nid in bad:
                report.findings.append(RuleFinding(severity, fam.id, rid, message,
                                                   f"node {nid} {g.class_of(nid)}.{require.get('input')}"
                                                   f" = {_show(g, nid, require.get('input', ''))}"))
            if candidates and not bad and any(v is None for v in verdicts.values()):
                report.not_judged.append(f"{fam.id}/{rid}")
            return
        if any(ok is True for ok in verdicts.values()):
            return
        if any(ok is None for ok in verdicts.values()):
            report.not_judged.append(f"{fam.id}/{rid}")
            return
        where = (f"node {triggers[0]} {g.class_of(triggers[0])}" +
                 (f"; found {', '.join(f'{n}={_show(g, n, require.get('input', ''))}' for n in candidates[:3])}"
                  if candidates else f"; no {require.get('node')} in the graph"))
        report.findings.append(RuleFinding(severity, fam.id, rid, message, where))

    def _upstream(self, fam, rule, rid, severity, message, g: ApiGraph, report: RuleReport) -> None:
        when, require, via = rule.get("when") or {}, rule.get("require") or {}, str(rule.get("via") or "model")
        for nid in g.of_class(when.get("node", "")):
            if _holds(g, nid, when, any_input=True) is not True:
                continue
            chain = g.upstream_chain(nid, via)
            wanted = ApiGraph.classes(require.get("node", ""))
            hits = [c for c in chain if g.class_of(c) in wanted]
            if any(_holds(g, c, require, any_input=False) is not False for c in hits):
                continue
            report.findings.append(RuleFinding(severity, fam.id, rid, message,
                                               f"node {nid} {g.class_of(nid)} reaches "
                                               + (", ".join(f"{c} {g.class_of(c)}" for c in hits) or "no " + require.get("node", ""))))

    # ------------------------------------------------------------------ numeric

    def _numeric(self, fam, rule, rid, severity, message, g: ApiGraph, report: RuleReport) -> None:
        on, spec = rule.get("on") or {}, dict(rule.get("numeric") or {})
        known = {"min", "max", "in", "multiple_of", "modulo"}
        if not set(spec) & known or not _input_expression_ok(on.get("input", "")):
            report.not_judged.append(f"{fam.id}/{rid}")  # an advisory formula, not a checkable bound
            return
        for nid in g.of_class(on.get("node", "")):
            for name, value in _values(g, nid, on.get("input", "")):
                if value is _UNKNOWN:
                    report.not_judged.append(f"{fam.id}/{rid}")
                    continue
                if value is None:
                    continue  # this node does not carry the input
                if not _numeric_ok(value, spec):
                    report.findings.append(RuleFinding(severity, fam.id, rid, message,
                                                       f"node {nid} {g.class_of(nid)}.{name} = {value!r}"))

    # ------------------------------------------------------------------ version

    def _version(self, fam, rule, rid, severity, message, version, recipe, report: RuleReport) -> None:
        have = version_tuple(version)
        if have == (0, 0, 0):
            report.not_judged.append(f"{fam.id}/{rid}")
            return
        needs = []
        if rule.get("min_comfyui"):
            needs.append(("the family", version_tuple(str(rule["min_comfyui"])), str(rule["min_comfyui"])))
        if rule.get("per_recipe") and recipe is not None and recipe.family == fam.id:
            needs.append((f"recipe {recipe.id}", recipe.min_comfyui, str(recipe.meta.get("min_comfyui"))))
        for who, need, text in needs:
            if need > have:
                report.findings.append(RuleFinding(severity, fam.id, rid, message,
                                                   f"{who} needs ComfyUI {text}; this one is {version}"))

    # ------------------------------------------------------------------ machine

    @staticmethod
    def _machine_fit(fam: FamilyProfile, g: ApiGraph, vram_gb: float) -> list[RuleFinding]:
        out = []
        for name in sorted(g.strings()):
            rec = fam.file(name)
            fits = ((rec or {}).get("vram") or {}).get("fits_gb")
            if isinstance(fits, (int, float)) and fits > vram_gb + 0.5:
                out.append(RuleFinding(WARN, fam.id, "machine-fit", (
                    f"{name} is only known to run on cards of {fits:g} GB ({(rec.get('vram') or {}).get('evidence', '')[:160]}); "
                    f"this one has {vram_gb:.0f} GB — expect offloading or out-of-memory; a smaller "
                    "variant in the profile may fit")))
        return out


# ---------------------------------------------------------------------- condition matching

def _input_expression_ok(expr: str) -> bool:
    """A plain input name, `a|b`, or `a x b` — anything else is prose (an advisory formula)."""
    return bool(re.fullmatch(r"[\w.]+(\s*\|\s*[\w.]+)*|[\w.]+\s+x\s+[\w.]+", str(expr).strip()))


def _values(g: ApiGraph, nid: str, expr: str) -> list[tuple[str, object]]:
    """[(input name, value)] for one input expression on one node. A missing input is None; a
    computed one is _UNKNOWN. `a x b` is the string "AxB" of two literals."""
    expr = str(expr).strip()
    if " x " in expr:
        a, b = (s.strip() for s in expr.split(" x ", 1))
        va, vb = _lit(g, nid, a), _lit(g, nid, b)
        if va is None or vb is None:
            return [(expr, None)]
        if va is _UNKNOWN or vb is _UNKNOWN:
            return [(expr, _UNKNOWN)]
        return [(expr, f"{va}x{vb}")]
    return [(name, _lit(g, nid, name)) for name in (s.strip() for s in expr.split("|"))]


def _lit(g: ApiGraph, nid: str, name: str):
    if name not in g.inputs(nid):
        return None
    v = g.literal(nid, name)
    return _UNKNOWN if v is None else v


def _holds(g: ApiGraph, nid: str, cond: dict, any_input: bool) -> bool | None:
    """Does node `nid` satisfy `cond`? True / False, or None when it cannot be known here."""
    names = [s.strip() for s in str(cond.get("input", "")).split("|") if s.strip()]
    if " x " in str(cond.get("input", "")):
        vals = [v for _n, v in _values(g, nid, cond["input"])]
        results = [_value_holds(g, nid, "", v, cond) for v in vals]
    else:
        # Of several alternative inputs (`unet_name|ckpt_name`) only those the node HAS are asked;
        # the others belong to the other classes in the pattern.
        if len(names) > 1:
            present = [n for n in names if n in g.inputs(nid)]
            names = present or names[:1]
        results = [_input_holds(g, nid, n, cond) for n in names] or [True]
    if any_input:
        if any(r is True for r in results):
            return True
        return None if any(r is None for r in results) else False
    if any(r is False for r in results):
        return False
    return None if any(r is None for r in results) else True


def _input_holds(g: ApiGraph, nid: str, name: str, cond: dict) -> bool | None:
    present = name in g.inputs(nid)
    if cond.get("exists") is not None and present != bool(cond["exists"]):
        return False
    raw = g.value(nid, name)
    if cond.get("linked") is not None and ApiGraph.is_link(raw) != bool(cond["linked"]):
        return False
    if cond.get("linked_from"):
        src = g.source(nid, name)
        if not src or g.class_of(src[0]) not in ApiGraph.classes(cond["linked_from"]):
            return False
    tests = {k for k in ("equals", "matches", "in", "min", "max", "multiple_of", "modulo") if k in cond}
    if not tests:
        return True if (present or cond.get("exists") is None) else False
    if not present:
        return False
    v = g.literal(nid, name)
    if v is None:
        return None
    return _value_holds(g, nid, name, v, cond)


def _value_holds(g, nid, name, v, cond: dict) -> bool | None:
    if v is _UNKNOWN:
        return None
    if v is None:
        return False
    if "equals" in cond and not _eq(v, cond["equals"]):
        return False
    if "matches" in cond and not (isinstance(v, str) and re.search(str(cond["matches"]), v)):
        return False
    if "in" in cond and not any(_eq(v, x) for x in cond["in"]):
        return False
    return _numeric_ok(v, {k: cond[k] for k in ("min", "max", "multiple_of", "modulo") if k in cond})


def _numeric_ok(v, spec: dict) -> bool:
    if "in" in spec and not any(_eq(v, x) for x in spec["in"]):
        return False
    bounds = {k: spec[k] for k in ("min", "max", "multiple_of", "modulo") if k in spec}
    if not bounds:
        return True
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    if "min" in bounds and x < float(bounds["min"]) - 1e-9:
        return False
    if "max" in bounds and x > float(bounds["max"]) + 1e-9:
        return False
    if "multiple_of" in bounds and float(bounds["multiple_of"]) and abs(x / float(bounds["multiple_of"]) - round(x / float(bounds["multiple_of"]))) > 1e-9:
        return False
    if "modulo" in bounds:
        m, r = bounds["modulo"]
        if int(x) % int(m) != int(r) % int(m):
            return False
    return True


def _eq(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a) == str(b)


def _show(g: ApiGraph, nid: str, name: str) -> str:
    v = g.value(nid, name)
    return f"linked from {v[0]}" if ApiGraph.is_link(v) else repr(v)


__all__ = ["ERROR", "WARN", "FamilyRuleValidator", "RuleFinding", "RuleReport"]
