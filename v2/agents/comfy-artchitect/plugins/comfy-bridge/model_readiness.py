"""Pure interpretation of ComfyUI's current loader inventory, shared by tools."""

from pathlib import PurePosixPath


class ModelReadiness:
    EXTENSIONS = (".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx")
    FIELDS = frozenset({
        "ckpt_name", "unet_name", "lora_name", "vae_name", "clip_name", "clip_name1",
        "clip_name2", "clip_name3", "control_net_name", "style_model_name",
        "upscale_model_name", "gligen_name",
    })
    DIRECTORY_FIELDS = {
        "checkpoints": {"ckpt_name"}, "diffusion_models": {"unet_name"},
        "vae": {"vae_name"}, "text_encoders": {"clip_name", "clip_name1", "clip_name2", "clip_name3"},
        "loras": {"lora_name"}, "controlnet": {"control_net_name"},
        "upscale_models": {"upscale_model_name"},
    }

    def __init__(self, catalogue):
        if not isinstance(catalogue, dict) or not catalogue:
            raise ValueError("ComfyUI loader inventory unavailable; model readiness is unknown")
        self.catalogue = catalogue

    @classmethod
    def looks_like_model_list(cls, values):
        if not isinstance(values, list) or not values:
            return False
        names = [value for value in values if isinstance(value, str)]
        hits = sum(value.lower().endswith(cls.EXTENSIONS) for value in names)
        return bool(names) and hits >= max(1, len(names) // 2)

    @classmethod
    def is_model_input(cls, field, choices, value=""):
        return (field in cls.FIELDS or cls.looks_like_model_list(choices)
                or (not choices and value.lower().endswith(cls.EXTENSIONS)))

    @staticmethod
    def input_specs(spec):
        return {field: value for section in ("required", "optional")
                for field, value in ((spec.get("input") or {}).get(section) or {}).items()}

    def model_enums(self):
        for node_class, spec in self.catalogue.items():
            if not isinstance(spec, dict):
                continue
            for field, entry in self.input_specs(spec).items():
                choices = entry[0] if isinstance(entry, list) and entry else None
                if isinstance(choices, list) and self.is_model_input(field, choices):
                    yield node_class, field, [v for v in choices if isinstance(v, str)]

    def names(self):
        names = {}
        for _, _, choices in self.model_enums():
            for value in choices:
                if value.lower().endswith(self.EXTENSIONS):
                    names.setdefault(PurePosixPath(value.replace("\\", "/")).name.lower(), value)
        return names

    def missing(self, graph):
        """Use each actual loader's enum, not another folder's same-named model."""
        missing = []
        for node_id, node in graph.items():
            node_class = node["class_type"]
            spec = self.catalogue.get(node_class)
            if not isinstance(spec, dict):
                raise ValueError(f"Cannot check model readiness: node {node_id} class {node_class} is unavailable")
            specs = self.input_specs(spec)
            for field, value in node.get("inputs", {}).items():
                if not isinstance(value, str):
                    continue  # connections are checked by the graph validator
                entry = specs.get(field)
                choices = entry[0] if isinstance(entry, list) and entry else None
                if (isinstance(choices, list) and self.is_model_input(field, choices, value)
                        and value not in choices):
                    missing.append(f"{value} (node {node_id}, {node_class}.{field})")
        return missing

    def contains(self, request):
        fields = self.DIRECTORY_FIELDS[request.directory]
        return any(
            PurePosixPath(value.replace("\\", "/")).name == request.filename
            for _, field, choices in self.model_enums() if field in fields
            for value in choices
        )
