"""Pure dependency selection from the validated graph and observed registries."""

import hashlib
import json
import re
from pathlib import PurePosixPath

from installer_source_policy import InstallerSourcePolicy
from model_download_request import ModelDownloadRequest
from model_readiness import ModelReadiness


class WorkflowInstallerManifest:
    @staticmethod
    def pack_key(value):
        return re.sub(r"[-_]", "", str(value)).lower()

    @staticmethod
    def fingerprint(graph):
        return hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()

    def build(self, graph, catalogue, recorded, models, packs):
        downloads, nodes, unresolved, references, paid = {}, {}, set(), set(), set()
        for node in graph.values():
            cls = node["class_type"]
            spec = catalogue.get(cls, {})
            if spec.get("api_node"):
                paid.add(cls)
            module = str(spec.get("python_module", ""))
            if module.startswith("custom_nodes."):
                module_id = module.split(".")[1]
                candidates = [entry for key, entry in packs.items()
                              if key == spec.get("cnr_id") or self.pack_key(key) == self.pack_key(module_id)
                              or self.pack_key(PurePosixPath(str(entry.get("repository", "")).removesuffix(".git")).name)
                              == self.pack_key(module_id)]
                repos = {str(e.get("repository", "")) for e in candidates}
                if len(repos) == 1:
                    try:
                        repo = InstallerSourcePolicy.repository_url(repos.pop())
                        name = InstallerSourcePolicy.relative_path(repo.rsplit("/", 1)[1])
                        nodes[repo] = {"repository": repo, "directory": name,
                                       "revision": None, "evidence": "ComfyUI-Manager registry"}
                    except ValueError:
                        unresolved.add(f"Node pack source for {cls}")
                else:
                    unresolved.add(f"Node pack source for {cls}")
            elif not (module == "nodes" or module.startswith(("comfy_extras.", "comfy_api_nodes."))):
                unresolved.add(f"Node origin for {cls} (not confirmed as built-in)")
            specs = ModelReadiness.input_specs(spec)
            for field, value in node.get("inputs", {}).items():
                if not isinstance(value, str):
                    continue
                if value.startswith("@") or field in ("image", "video", "audio"):
                    references.add(f"{cls}.{field}: {value}")
                shape = specs.get(field)
                choices = shape[0] if isinstance(shape, list) and shape else None
                if not (value.lower().endswith(ModelReadiness.EXTENSIONS)
                        and ModelReadiness.is_model_input(field, choices, value)):
                    continue
                name = value.replace("\\", "/")
                directory = next((d for d, fields in ModelReadiness.DIRECTORY_FIELDS.items() if field in fields), None)
                # clip_name is also used by image-encoder loaders; its actual output type
                # distinguishes their weights from a text encoder's.
                if field == "clip_name" and "CLIP_VISION" in spec.get("output", []):
                    directory = "clip_vision"
                candidates = [r for r in recorded if r["filename"] == name
                              and (directory is None or r["kind"] == directory)]
                if not candidates:
                    for entry in models:
                        if str(entry.get("filename", "")).replace("\\", "/") != name:
                            continue
                        kind_name = str(entry.get("type", "")).lower()
                        kind = ModelDownloadRequest.DIRECTORIES.get(kind_name) or {
                            "clip_vision": "clip_vision", "style_model": "style_models",
                            "embedding": "embeddings", "gligen": "gligen",
                        }.get(kind_name)
                        if directory and kind and kind != directory:
                            continue
                        folder = directory or kind
                        if folder:
                            candidates.append({"url": entry.get("url", ""),
                                               "destination": f"models/{folder}/{name}",
                                               "evidence": "ComfyUI-Manager model catalogue"})
                sources = {}
                for candidate in candidates:
                    try:
                        url = InstallerSourcePolicy.source_url(candidate["url"])
                        dest = InstallerSourcePolicy.relative_path(candidate["destination"])
                        sources[(url, dest)] = {"url": url, "destination": dest,
                                                "evidence": candidate["evidence"]}
                    except ValueError:
                        continue
                if len(sources) == 1:
                    item = next(iter(sources.values()))
                    previous = downloads.get(item["destination"])
                    if previous and previous["url"] != item["url"]:
                        unresolved.add(f"Conflicting sources for {name}")
                    downloads[item["destination"]] = item
                else:
                    unresolved.add(f"Model source/destination for {cls}.{field}: {name}")
        return {"schema_version": 1, "workflow_sha256": self.fingerprint(graph),
                "models": sorted(downloads.values(), key=lambda r: r["destination"]),
                "node_packs": sorted(nodes.values(), key=lambda r: r["repository"]),
                "unresolved": sorted(unresolved), "references": sorted(references),
                "paid_api_nodes": sorted(paid),
                "notes": ["Use your existing ComfyUI Python environment. Stop ComfyUI before installing.",
                          "Unpinned node repositories use their current default branch; compatibility is not guaranteed.",
                          "Custom install.py scripts and OS dependencies are NOT run automatically; consult each pack's README.",
                          "Supply your own input/reference media and configure paid-node accounts yourself.",
                          "This installs dependencies only. It never renders or spends Comfy.org credits."]}
