"""StageLoraSplicer — a stage's LoRAs, spliced into its graph right after the model they modify.

Where they go is the family's (its profile's `lora` block, from the official templates):

  * the LOADER node: LoraLoaderModelOnly on every DiT family (FLUX, Qwen, Z-Image, Wan, LTX, …);
    LoraLoader on the SD1.5/SDXL lineage, which also patches the checkpoint's CLIP (per model where
    a family mixes both);
  * which MODEL loaders take it: those whose file matches one of `models[].file` — never an
    upscaler, a depth estimator or a pose detector loaded beside the model;
  * on a two-expert model (Wan 2.2 14B) each LoRA names its expert: 'high' goes on the high-noise
    model only, 'low' on the low-noise one — the way the official templates wire LoRA pairs.

Each LoRA takes the loader's MODEL output and everything that read it reads the last LoRA instead,
so a speed LoRA the recipe already chains (Lightning, Turbo) comes after the user's LoRA — the
order the official templates use. Pure: the graph is changed in place, nothing is read or written.
"""

from __future__ import annotations

import re

from api_graph import ApiGraph
from stage_lora import StageLora

#: The loaders whose output 0 is the MODEL a LoRA modifies, and the input that names the file.
MODEL_LOADERS = {"UNETLoader": "unet_name", "CheckpointLoaderSimple": "ckpt_name"}


class StageLoraSplicer:
    @staticmethod
    def splice(stage_name: str, family_id: str, spec: dict, loras: list[StageLora], graph: dict) -> list[str]:
        """Add the LoRA nodes; returns their node ids. ValueError when the stage cannot take them."""
        if not loras:
            return []
        if not spec:
            raise ValueError(f"stage {stage_name}: {family_id} takes no LoRA in this knowledge base")
        targets = StageLoraSplicer.targets(spec, graph)
        if not targets:
            raise ValueError(f"stage {stage_name}: no model loader in this recipe takes a LoRA "
                             f"(the {family_id} LoRA models are: "
                             f"{', '.join(m.get('file', '') for m in spec.get('models') or [])})")
        two_experts = any(t["expert"] for t in targets)
        for lo in loras:
            if two_experts and not lo.expert:
                raise ValueError(f"stage {stage_name}: this model has two experts — LoRA {lo.name} must say "
                                 "expert 'high' (the high-noise model) or 'low' (the low-noise model); a Wan 2.2 "
                                 "LoRA comes as a high/low pair")
            if lo.expert and not two_experts:
                raise ValueError(f"stage {stage_name}: LoRA {lo.name} names expert {lo.expert!r}, but this "
                                 "model has a single expert — leave expert empty")
        added: list[str] = []
        next_id = max((int(n) for n in graph if str(n).isdigit()), default=0) + 1
        for target in targets:
            chain = [lo for lo in loras if not two_experts or lo.expert == target["expert"]]
            if not chain:
                continue
            loader = target["loader"]
            model_src, clip_src = [target["node"], 0], [target["node"], 1]
            model_readers = StageLoraSplicer._readers(graph, target["node"], 0)
            clip_readers = StageLoraSplicer._readers(graph, target["node"], 1) if loader == "LoraLoader" else []
            for lo in chain:
                nid = str(next_id)
                next_id += 1
                if loader == "LoraLoader":
                    graph[nid] = {"class_type": "LoraLoader",
                                  "inputs": {"model": model_src, "clip": clip_src, "lora_name": lo.name,
                                             "strength_model": lo.strength, "strength_clip": lo.strength},
                                  "_meta": {"title": f"LoRA {lo.name}"}}
                    clip_src = [nid, 1]
                else:
                    graph[nid] = {"class_type": "LoraLoaderModelOnly",
                                  "inputs": {"model": model_src, "lora_name": lo.name, "strength_model": lo.strength},
                                  "_meta": {"title": f"LoRA {lo.name}"}}
                model_src = [nid, 0]
                added.append(nid)
            for node_id, field_name in model_readers:
                graph[node_id]["inputs"][field_name] = list(model_src)
            for node_id, field_name in clip_readers:
                graph[node_id]["inputs"][field_name] = list(clip_src)
        return added

    @staticmethod
    def targets(spec: dict, graph: dict) -> list[dict]:
        """[{node, file, expert, bases, cloud, loader}] — the model loaders of `graph` this family's
        LoRAs go on. A model's own `loader` overrides the family's (a family with SD1.5/SDXL and DiT
        models side by side)."""
        out = []
        for nid, node in graph.items():
            field_name = MODEL_LOADERS.get(node.get("class_type", "")) if isinstance(node, dict) else None
            if not field_name:
                continue
            file = str((node.get("inputs") or {}).get(field_name) or "").replace("\\", "/").rsplit("/", 1)[-1]
            for model in spec.get("models") or []:
                if re.search(str(model.get("file") or "$^"), file, re.I):
                    out.append({"node": nid, "file": file, "expert": str(model.get("expert") or ""),
                                "bases": list(model.get("bases") or []), "cloud": list(model.get("cloud") or []),
                                "loader": str(model.get("loader") or spec.get("loader") or "LoraLoaderModelOnly")})
                    break
        return out

    @staticmethod
    def _readers(graph: dict, node_id: str, slot: int) -> list[tuple[str, str]]:
        return [(nid, k) for nid, node in graph.items() for k, v in (node.get("inputs") or {}).items()
                if ApiGraph.is_link(v) and str(v[0]) == str(node_id) and int(v[1]) == slot]


__all__ = ["MODEL_LOADERS", "StageLoraSplicer"]
