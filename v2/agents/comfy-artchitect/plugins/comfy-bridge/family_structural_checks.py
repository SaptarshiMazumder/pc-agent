"""FamilyStructuralChecks — the knowledge base's rules that need the graph WALKED, not just read.

Most rules in a family profile are data (`pair`, `numeric`, `upstream`) and FamilyRuleValidator
evaluates them generically. A few are about how nodes connect — two samplers handing a latent over
at the same step, a reference latent reaching both CFG branches, a guide frame inside the clip — and
those are named in the profile (`"kind": "structural", "structural": "<name>", "args": {...}`) and
implemented here, one method per name.

Each check returns a list of problems ([] = holds), or None when the graph alone cannot say (a
frame count that arrives with a user's video, which of two packs is installed). None is reported as
"not judged here", never as a pass.
"""

from __future__ import annotations

import re

from api_graph import ApiGraph
from family_profile import FamilyProfile

_DECODE = ("VAEDecode", "VAEDecodeTiled", "VAEDecodeHunyuan3D")


class FamilyStructuralChecks:
    def names(self) -> set[str]:
        return {n for n in dir(self) if not n.startswith("_") and n not in ("names", "run")}

    def run(self, name: str, graph: ApiGraph, args: dict, profile: FamilyProfile) -> list[str] | None:
        """Raises KeyError for a check this code does not have — a profile naming one is a defect
        to surface, not a rule to skip."""
        if name not in self.names():
            raise KeyError(name)
        return getattr(self, name)(graph, args or {}, profile)

    # ------------------------------------------------------------------ two-stage sampling

    def moe_two_expert_split(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        """Wan 2.2 14B: the high-noise expert's sampler hands its noisy latent to the low-noise one."""
        return _handoff(g, a.get("loader", "UNETLoader"), a.get("high", ""), a.get("low", ""),
                        a.get("sampler", "KSamplerAdvanced"), "high-noise", "low-noise")

    def refiner_handoff(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        """SDXL base + refiner: the base sampler stops early and the refiner finishes it."""
        return _handoff(g, a.get("loader", "CheckpointLoaderSimple"), a.get("base", ""), a.get("refiner", ""),
                        a.get("sampler", "KSamplerAdvanced"), "base", "refiner")

    def moe_both_experts_present(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        high = _loaders_matching(g, a.get("loader", "UNETLoader"), a.get("high", ""))
        low = _loaders_matching(g, a.get("loader", "UNETLoader"), a.get("low", ""))
        if bool(high) != bool(low):
            have, missing = ("high-noise", "low-noise") if high else ("low-noise", "high-noise")
            return [f"the {have} model is loaded without its {missing} partner"]
        return []

    def sampler_output_slot(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        """With guide frames in the latent, decode the sampler's DENOISED output, not its raw one."""
        if not g.of_class(a.get("when_node", "")):
            return []
        slot, out = int(a.get("slot", 1)), []
        for sid in g.of_class(a.get("sampler", "SamplerCustomAdvanced")):
            for cid, name in g.consumers(sid):
                cls = g.class_of(cid)
                if (cls in _DECODE or "CropGuides" in cls or "SeparateAVLatent" in cls) and g.value(cid, name)[1] != slot:
                    out.append(f"node {cid} ({cls}).{name} reads output {g.value(cid, name)[1]} of sampler "
                               f"{sid}; it must read output {slot} (denoised)")
        return out

    # ------------------------------------------------------------------ wiring

    def vace_trim_wired(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        out = []
        slot = int(a.get("output", 3))
        for sid in g.of_class(a.get("source", "")):
            fed = [cid for cid, name in g.consumers(sid, slot)
                   if g.class_of(cid) in ApiGraph.classes(a.get("sink", "")) and name == a.get("input")]
            if not fed:
                out.append(f"{g.class_of(sid)} {sid} output {slot} feeds no {a.get('sink')}.{a.get('input')}")
        return out

    def iclora_parameters_wired(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        rx = re.compile(str(a.get("lora_regex", "")), re.I)
        uses = any(isinstance(v, str) and rx.search(v)
                   for nid in g.nodes for k, v in g.inputs(nid).items() if "lora" in k)
        if not uses:
            return []
        readers = set(g.of_class(a.get("reader", "")))
        wired = any(g.source(sid, a.get("input", "")) and g.source(sid, a.get("input", ""))[0] in readers
                    for sid in g.of_class(a.get("sink", "")))
        return [] if wired else [f"an IC-LoRA is loaded but no {a.get('sink')}.{a.get('input')} is fed by "
                                 f"{a.get('reader')}"]

    def ltx_av_latent_wrapped(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        if not g.of_class(a.get("concat", "")):
            return []
        samplers = set(g.of_class(a.get("samplers", "")))
        fed = any(src and src[0] in samplers
                  for sep in g.of_class(a.get("separate", "")) for src in [_first_link_source(g, sep)])
        return [] if fed else [f"audio+video latents are concatenated ({a.get('concat')}) but no "
                               f"{a.get('separate')} splits a sampler's output back apart before decoding"]

    def ltx_crop_guides_wired(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        if not g.of_class(a.get("guide", "")):
            return []
        crops = set(g.of_class(a.get("crop", "")))
        decodes = g.of_class(a.get("decode", ""))
        if crops and any(crops & g.ancestors(d) for d in decodes):
            return []
        return [f"guide frames are added ({a.get('guide')}) but no {a.get('crop')} removes them before "
                f"{a.get('decode')}"]

    def flux2_scheduler_size_matches_latent(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        latents = g.of_class(a.get("latent", ""))
        if not latents:
            return []
        out = []
        for sid in g.of_class(a.get("scheduler", "")):
            for inp in a.get("inputs") or ["width", "height"]:
                if not any(_same(g, sid, inp, lid, inp) for lid in latents):
                    out.append(f"{g.class_of(sid)} {sid}.{inp} differs from the latent's {inp}: the "
                               "schedule is computed for a different image size")
        return out

    def reference_latent_on_both_branches(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        refs = set(g.of_class(a.get("reference", "ReferenceLatent")))
        out = []
        for gid in g.of_class(a.get("guider", "CFGGuider")):
            pos, neg = g.source(gid, "positive"), g.source(gid, "negative")
            if not pos or not neg:
                continue
            on_pos = _ref_latents(g, refs, pos[0])
            on_neg = _ref_latents(g, refs, neg[0])
            if on_pos - on_neg:
                out.append(f"guider {gid}: reference image(s) reach the positive branch but not the "
                           "negative — the edit drifts")
        return out

    def same_value(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        """Inputs that must agree. Four operands are two pairs (a=b, c=d); otherwise all equal."""
        ops = [a[k] for k in sorted(a) if isinstance(a.get(k), dict) and a[k].get("node")]
        groups = [ops[0:2], ops[2:4]] if sorted(a) == ["a", "b", "c", "d"] else [ops]
        out = []
        for group in groups:
            present = [(nid, o["input"]) for o in group for nid in g.of_class(o["node"])[:1]]
            for (n1, i1), (n2, i2) in zip(present, present[1:]):
                if not _same(g, n1, i1, n2, i2):
                    out.append(f"{g.class_of(n1)}.{i1} ({_show(g, n1, i1)}) and {g.class_of(n2)}.{i2} "
                               f"({_show(g, n2, i2)}) must be the same")
        return out

    # ------------------------------------------------------------------ limits and counts

    def autogrow_slot_limits(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        limits, out = dict(a.get("limits") or {}), []
        for nid in g.of_class(a.get("node", "")):
            counts = {grp: sum(1 for k in g.inputs(nid) if k.startswith(f"{grp}.")) for grp in limits}
            for grp, n in counts.items():
                if n > int(limits[grp]):
                    out.append(f"node {nid}: {n} {grp} wired; at most {limits[grp]}")
            if a.get("total") and sum(counts.values()) > int(a["total"]):
                out.append(f"node {nid}: {sum(counts.values())} references wired; at most {a['total']} in all")
        return out

    def prompt_tags_within_connected_refs(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str] | None:
        out = []
        for nid in g.of_class(a.get("node", "")):
            text = g.literal(nid, a.get("input", "prompt"))
            if not isinstance(text, str):
                return None
            for tag, groups in (a.get("tags") or {}).items():
                connected = sum(1 for grp in str(groups).split("+") for k in g.inputs(nid) if k.startswith(f"{grp}."))
                for m in re.finditer(tag, text):
                    if int(m.group(1)) > connected:
                        out.append(f"node {nid}: the prompt names {m.group(0)} but only {connected} such "
                                   "reference(s) are wired")
        return out

    def guide_fits_length(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str] | None:
        sources = g.of_class(a.get("source", ""))
        if not sources or not g.of_class(a.get("guide", "")):
            return []
        length = g.literal(sources[0], a.get("length", "length"))
        if not isinstance(length, int):
            return None
        out = []
        for gid in g.of_class(a.get("guide", "")):
            idx = g.literal(gid, a.get("index", "frame_idx"))
            if isinstance(idx, int) and idx >= length:
                out.append(f"guide {gid} sits at frame {idx}, past the clip's {length} frames")
        return out

    def video_min_frames(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str] | None:
        out = []
        for nid in g.of_class(a.get("node", "")):
            v = g.literal(nid, a.get("input", "frames"))
            if not isinstance(v, int):
                return None  # the frames arrive with the video: judged on the box
            if v < int(a.get("min_frames", 0)):
                out.append(f"node {nid}: {v} frames; this needs at least {a['min_frames']}")
        return out

    def input_less_than_half_of_other(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        out = []
        for nid in g.of_class(a.get("node", "")):
            x, y = g.literal(nid, a.get("input", "")), g.literal(nid, a.get("other", ""))
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and y and x >= y / 2:
                out.append(f"node {nid}: {a['input']} {x} must be under half of {a['other']} {y}")
        return out

    def sd3_negative_minimal(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        allowed = set(a.get("allowed_sources") or [])
        limit, out = int(a.get("max_words", 3)), []
        for sid in g.of_class(a.get("sampler", "KSampler")):
            src = g.source(sid, a.get("input", "negative"))
            if not src or g.class_of(src[0]) in allowed:
                continue
            words = max((len(str(v).split()) for k, v in g.inputs(src[0]).items()
                         if isinstance(v, str) and k in ("text", "clip_l", "clip_g", "t5xxl")), default=0)
            if words > limit:
                out.append(f"sampler {sid}: a {words}-word negative prompt; this family works best with an "
                           "empty or near-empty one")
        return out

    def prompt_has_sections(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        out = []
        for node_key, markers_key in (("node", "markers"), ("ref_node", "ref_markers")):
            markers = p.dotted(a.get(markers_key, "")) if a.get(markers_key) else None
            if not a.get(node_key) or not isinstance(markers, list):
                continue
            for nid in g.of_class(a[node_key]):
                text = g.literal(nid, a.get("input", "prompt"))
                if not isinstance(text, str):
                    continue
                missing = [m for m in markers if str(m).lower() not in text.lower()]
                if missing:
                    out.append(f"node {nid}: the prompt lacks the family's own sections: {', '.join(missing)} "
                               f"(see the {p.name} guide)")
        return out

    def prompt_times_within_length(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str] | None:
        """Every time the prompt names (a shot starting "At 00:05.500", "the 5.50-second mark") is
        inside the clip. A shot timed past the end never happens — the action it carries is lost.
        args: node (prompt + length), input, length, fps_node / fps_input (where the frame rate is)."""
        fps_nodes = g.of_class(a.get("fps_node", ""))
        fps = g.literal(fps_nodes[0], a.get("fps_input", "fps")) if fps_nodes else a.get("fps")
        out = []
        for nid in g.of_class(a.get("node", "")):
            text, length = g.literal(nid, a.get("input", "prompt")), g.literal(nid, a.get("length", "length"))
            if not isinstance(text, str):
                continue
            if not isinstance(length, int) or not isinstance(fps, (int, float)) or not fps:
                return None  # the clip's duration arrives at run time
            seconds = length / float(fps)
            late = sorted({t for t in _prompt_times(text) if t > seconds + 0.05})
            if late:
                out.append(f"node {nid}: the prompt times something at {', '.join(f'{t:.2f} s' for t in late)}, "
                           f"but the clip is {length} frames at {fps:g} fps = {seconds:.2f} s — re-time the "
                           "shots to the clip, or lengthen it")
        return out

    def prompt_avoids_negation(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        """For families whose guide says a negation ADDS what it names ("no subtitles" puts
        subtitles in): the positive prompt says what IS in the shot. Quoted text and dialogue are
        left alone — they are words someone says or writes. args: node, input(s)."""
        inputs = [a["input"]] if isinstance(a.get("input"), str) else list(a.get("inputs") or ["prompt"])
        out = []
        for nid in g.of_class(a.get("node", "")):
            for name in inputs:
                text = g.literal(nid, name)
                if not isinstance(text, str):
                    continue
                spoken = re.sub(r"<d>.*?</d>|\"[^\"]*\"|“[^”]*”", " ", text, flags=re.S)
                found = sorted({m.group(0).lower() for m in _NEGATION.finditer(spoken)})
                if found:
                    out.append(f"node {nid}.{name}: negations in the prompt ({', '.join(found)}) — this model "
                               "adds what a negation names; describe what IS in the shot instead")
        return out

    def prompt_marker_with_file(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str]:
        """When the graph loads a given file (a LoRA with a trigger), every prompt on the given node
        class carries its marker. args: file, node, input, marker, skip_empty (a negative encoder)."""
        if not any(_file_base(s) == _file_base(a.get("file", "")) for s in g.strings()):
            return []
        out = []
        for nid in g.of_class(a.get("node", "")):
            text = g.literal(nid, a.get("input", "prompt"))
            if not isinstance(text, str) or (a.get("skip_empty") and not text.strip()):
                continue
            markers = list(a.get("markers") or [a.get("marker", "")])
            if not any(m.lower() in text.lower() for m in markers):
                out.append(f"node {nid}.{a.get('input', 'prompt')}: none of {', '.join(repr(m) for m in markers)} in "
                           f"the prompt, which {a.get('file')} needs")
        return out

    def control_image_matches_latent(self, g: ApiGraph, a: dict, p: FamilyProfile) -> list[str] | None:
        """Only when both sizes are written in the graph; otherwise they arrive with the images."""
        latents = [n for n in g.of_class(a.get("latent", "")) if isinstance(g.literal(n, "width"), int)]
        scalers = [n for n in g.of_class("ImageScale") if isinstance(g.literal(n, "width"), int)]
        if not latents or not scalers:
            return None
        lw, lh = g.literal(latents[0], "width"), g.literal(latents[0], "height")
        out = []
        for s in scalers:
            if (g.literal(s, "width"), g.literal(s, "height")) != (lw, lh):
                out.append(f"control image is scaled to {g.literal(s, 'width')}x{g.literal(s, 'height')}, "
                           f"the latent is {lw}x{lh}")
        return out

    # ------------------------------------------------------------------ not judgeable from a graph

    def packs_mutually_exclusive(self, g: ApiGraph, a: dict, p: FamilyProfile) -> None:
        return None  # which pack provides the shared classes is a fact about the box

    def upscale_model_is_single_image(self, g: ApiGraph, a: dict, p: FamilyProfile) -> None:
        return None  # the architecture is read from the weights when they load

    def upscale_output_matches_target(self, g: ApiGraph, a: dict, p: FamilyProfile) -> None:
        return None  # the target size is the user's, not the graph's


# ---------------------------------------------------------------------- helpers

#: Negation as a prompt word, not inside another word ("note", "nothing" included on purpose).
_NEGATION = re.compile(r"\b(?:no|not|without|never|nothing|don't|do not|avoid)\b", re.I)
#: "00:05.500" (mm:ss, two digits each — "9:16" is an aspect ratio, not a time) and "5.50-second" /
#: "5.5 seconds" / "5.50s mark".
_CLOCK = re.compile(r"(?<![\d:])(\d{2}):(\d{2}(?:\.\d+)?)(?![\d:])")
_SECONDS = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:-?\s*seconds?\b|s\s+mark\b)", re.I)


def _file_base(value: str) -> str:
    return str(value).replace("\\", "/").rsplit("/", 1)[-1].lower()


def _prompt_times(text: str) -> list[float]:
    times = [int(m.group(1)) * 60 + float(m.group(2)) for m in _CLOCK.finditer(text)]
    return times + [float(m.group(1)) for m in _SECONDS.finditer(text)]


def _loaders_matching(g: ApiGraph, loader: str, needle: str) -> list[str]:
    return [nid for nid in g.of_class(loader)
            if needle and any(isinstance(v, str) and needle.lower() in v.lower() for v in g.inputs(nid).values())]


def _sampler_for(g: ApiGraph, samplers: list[str], loaders: list[str]) -> list[str]:
    return [s for s in samplers if set(g.upstream_chain(s, "model")) & set(loaders)]


def _handoff(g: ApiGraph, loader: str, first: str, second: str, sampler: str, n1: str, n2: str) -> list[str]:
    a_loaders, b_loaders = _loaders_matching(g, loader, first), _loaders_matching(g, loader, second)
    if not a_loaders or not b_loaders:
        return []
    samplers = g.of_class(sampler)
    a_s, b_s = _sampler_for(g, samplers, a_loaders), _sampler_for(g, samplers, b_loaders)
    if not a_s or not b_s:
        return [f"the {n1} and {n2} models must each run in their own {sampler}"]
    out, s1, s2 = [], a_s[0], b_s[0]
    lit = g.literal
    if lit(s1, "add_noise") != "enable":
        out.append(f"{n1} sampler {s1}: add_noise must be enable")
    if lit(s1, "return_with_leftover_noise") != "enable":
        out.append(f"{n1} sampler {s1}: return_with_leftover_noise must be enable (it hands over a noisy latent)")
    if lit(s2, "add_noise") != "disable":
        out.append(f"{n2} sampler {s2}: add_noise must be disable (the latent already carries the noise)")
    if lit(s2, "return_with_leftover_noise") != "disable":
        out.append(f"{n2} sampler {s2}: return_with_leftover_noise must be disable")
    if g.source(s2, "latent_image") is None or g.source(s2, "latent_image")[0] != s1:
        out.append(f"{n2} sampler {s2} must take its latent from the {n1} sampler {s1}")
    end, start = lit(s1, "end_at_step"), lit(s2, "start_at_step")
    if isinstance(end, int) and isinstance(start, int) and end != start:
        out.append(f"the {n1} sampler stops at step {end} but the {n2} sampler starts at {start}: they must meet")
    if lit(s1, "start_at_step") not in (0, None):
        out.append(f"{n1} sampler {s1}: start_at_step must be 0")
    if isinstance(lit(s1, "steps"), int) and isinstance(lit(s2, "steps"), int) and lit(s1, "steps") != lit(s2, "steps"):
        out.append(f"both samplers must count the same total steps ({lit(s1, 'steps')} vs {lit(s2, 'steps')})")
    return out


def _first_link_source(g: ApiGraph, nid: str) -> tuple[str, int] | None:
    for v in g.inputs(nid).values():
        if ApiGraph.is_link(v) and v[0] in g.nodes:
            return v[0], v[1]
    return None


def _ref_latents(g: ApiGraph, refs: set[str], start: str) -> set[str]:
    """The latents ReferenceLatent nodes attach on the branch ending at `start`."""
    nodes = ({start} | g.ancestors(start)) & refs
    return {str(g.value(r, "latent")[0]) for r in nodes if ApiGraph.is_link(g.value(r, "latent"))}


def _same(g: ApiGraph, n1: str, i1: str, n2: str, i2: str) -> bool:
    v1, v2 = g.value(n1, i1), g.value(n2, i2)
    if ApiGraph.is_link(v1) and ApiGraph.is_link(v2):
        return v1 == v2
    l1, l2 = g.literal(n1, i1), g.literal(n2, i2)
    if l1 is None or l2 is None:
        return True  # computed at run time: not judged here
    try:
        return abs(float(l1) - float(l2)) < 1e-6
    except (TypeError, ValueError):
        return l1 == l2


def _show(g: ApiGraph, nid: str, name: str) -> str:
    v = g.value(nid, name)
    return f"linked from {v[0]}" if ApiGraph.is_link(v) else repr(v)


__all__ = ["FamilyStructuralChecks"]
