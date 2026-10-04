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
                        and value not in choices and not self.spelled(value, choices)):
                    missing.append(f"{value} (node {node_id}, {node_class}.{field})")
        return missing

    @staticmethod
    def spelled(value, choices):
        """How this instance lists the SAME FILE `value` names, when it lists it differently —
        else None. The same file is the same name: with the other slash (a Windows-made workflow
        on a Linux ComfyUI), or in another folder (`qwen-image/qwen_image_vae.safetensors` for
        `qwen_image_vae.safetensors`, `lora.safetensors` for `Krea2/lora.safetensors`) — which is
        what a person does in the editor when a workflow from elsewhere names a file they have.
        Only when exactly one listed file has that name; two are a choice, not a spelling."""
        if not isinstance(value, str) or not isinstance(choices, list):
            return None
        want = value.replace("\\", "/")
        base = want.rsplit("/", 1)[-1]
        same_path = [c for c in choices if isinstance(c, str) and c != value and c.replace("\\", "/") == want]
        if same_path:
            return same_path[0]
        same_name = [c for c in choices if isinstance(c, str) and c != value
                     and c.replace("\\", "/").rsplit("/", 1)[-1] == base]
        return same_name[0] if len(same_name) == 1 else None

    @staticmethod
    def closest(value, choices):
        """The listed file whose name is nearest `value` — a hint for a person, never a swap."""
        import difflib

        names = [c for c in choices if isinstance(c, str) and c.lower().endswith(ModelReadiness.EXTENSIONS)]
        base = value.replace("\\", "/").rsplit("/", 1)[-1]
        found = difflib.get_close_matches(base, [n.replace("\\", "/").rsplit("/", 1)[-1] for n in names],
                                          n=1, cutoff=0.8)
        if not found:
            return None
        return next(n for n in names if n.replace("\\", "/").rsplit("/", 1)[-1] == found[0])

    def respell(self, graph):
        """The graph with every model name written the way THIS instance lists it — what it
        will accept on /prompt. Only slash direction changes; a different file never does."""
        out = {}
        for node_id, node in graph.items():
            spec = self.catalogue.get(node.get("class_type")) if isinstance(node, dict) else None
            if not isinstance(spec, dict):
                out[node_id] = node
                continue
            specs = self.input_specs(spec)
            inputs = dict(node.get("inputs") or {})
            for field, value in inputs.items():
                entry = specs.get(field)
                choices = entry[0] if isinstance(entry, list) and entry else None
                spelling = self.spelled(value, choices)
                if spelling:
                    inputs[field] = spelling
            out[node_id] = {**node, "inputs": inputs}
        return out

    def contains(self, request):
        """Does a loader list this file? Folders without a known loader field (clip_vision,
        latent_upscale_models, a pack's own folder) are answered from every loader's list."""
        fields = self.DIRECTORY_FIELDS.get(request.directory)
        return any(
            PurePosixPath(value.replace("\\", "/")).name == request.basename
            for _, field, choices in self.model_enums() if fields is None or field in fields
            for value in choices
        )
