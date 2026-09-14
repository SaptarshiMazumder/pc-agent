"""Pure extraction/comparison of model companions from API or UI reference workflows."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re


@dataclass(frozen=True)
class WorkflowModelManifest:
    models: frozenset[str]
    vaes: frozenset[str]
    encoders: frozenset[str]

    @classmethod
    def from_graph(cls, graph: dict):
        found = {"models": set(), "vaes": set(), "encoders": set()}
        nodes = list(graph.get("nodes")) if isinstance(graph.get("nodes"), list) else list(graph.values())
        definitions = {g["id"]: g for g in (graph.get("definitions") or {}).get("subgraphs", [])}
        visited = set()
        # Current official templates put loaders inside named subgraphs. Walk referenced
        # definitions only: unused alternative stacks must not become required companions.
        for node in nodes:
            if isinstance(node, dict) and node.get("type") in definitions and node["type"] not in visited:
                visited.add(node["type"])
                nodes.extend(definitions[node["type"]].get("nodes") or [])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            kind = str(node.get("class_type") or node.get("type") or "").lower()
            fields = node.get("inputs")
            if isinstance(fields, dict):
                values = fields.items()
            else:
                # UI widget order varies with ComfyUI; category follows the loader class,
                # and only actual weight filenames are read from the widget values.
                field = ("vae_name" if "vaeloader" in kind else "clip_name" if
                         ("cliploader" in kind or "textencoderloader" in kind) else
                         "model_name" if ("unetloader" in kind or "checkpointloader" in kind) else "")
                values = [(field, v) for v in (node.get("widgets_values") or [])]
            for field, value in values:
                if not isinstance(value, str) or not value.lower().endswith(
                    (".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".bin", ".gguf")
                ):
                    continue
                category = ("vaes" if field == "vae_name" else "encoders" if
                            field.startswith(("clip_name", "text_encoder_name", "t5_name")) else
                            "models" if field in ("unet_name", "model_name", "ckpt_name") else "")
                if category:
                    found[category].add(PurePosixPath(value.replace("\\", "/")).name)
        return cls(**{key: frozenset(value) for key, value in found.items()})

    @property
    def needs_reference(self) -> bool:
        return bool(self.models and (self.vaes or self.encoders))

    def compare(self, reference: "WorkflowModelManifest") -> list[str]:
        problems = []
        if not reference.models:
            return ["Reference JSON contains no recognised model loader; fetch the actual workflow JSON"]
        reference_families = {self.model_family(name) for name in reference.models}
        unsupported = [name for name in self.models if self.model_family(name) not in reference_families]
        if unsupported:
            problems.append("Reference workflow is for a different model/version: "
                            + ", ".join(sorted(unsupported)) + " is not covered by "
                            + ", ".join(sorted(reference.models)))
        for label, actual, expected in (("VAE", self.vaes, reference.vaes),
                                        ("text encoder", self.encoders, reference.encoders)):
            if actual != expected:
                problems.append(f"{label} mismatch: workflow uses {', '.join(sorted(actual)) or '(none)'}; "
                                f"reference requires {', '.join(sorted(expected)) or '(none)'}")
        return problems

    @staticmethod
    def model_family(filename: str) -> str:
        """Allow explicit precision variants, never erase the model's name or version."""
        stem = re.sub(r"[-_.]+", "_", PurePosixPath(filename).stem.lower())
        return re.sub(r"_(?:bf16|fp16|fp32|fp8mixed|fp8(?:_scaled|_e4m3fn?|_e4m3|_e5m2)?|q[2-8](?:_k(?:_[msl])?|_[01])?)$", "", stem)
