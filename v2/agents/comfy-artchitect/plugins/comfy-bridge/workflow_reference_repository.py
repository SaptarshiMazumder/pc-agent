"""Fetch and retain a source workflow as evidence, keyed to the designed model stack."""

import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from workflow_model_manifest import WorkflowModelManifest


class WorkflowReferenceRepository:
    def __init__(self, root: Path, *, fetch):
        self.root = root
        self.fetch = fetch

    def check(self, graph: dict, url: str = "") -> list[str]:
        actual = WorkflowModelManifest.from_graph(graph)
        if not actual.needs_reference:
            return []
        # A changed model stack requires new evidence; sampling/prompt edits can reuse it.
        stack = [sorted(actual.models), sorted(actual.vaes), sorted(actual.encoders)]
        key = hashlib.sha256(json.dumps(stack).encode()).hexdigest()
        path = self.root / ".studio" / "model-references" / (key + ".json")
        if url:
            parsed = urlsplit(url)
            # A WORKFLOW IS A .json. The host check alone let a huggingface.co `/resolve/` link to a
            # checkpoint through, which this then read as text.
            if (parsed.scheme != "https" or parsed.username or parsed.password
                    or parsed.port not in (None, 443)
                    or parsed.hostname not in ("raw.githubusercontent.com", "huggingface.co")
                    or not parsed.path.lower().endswith(".json")):
                return ["reference_workflow_url must be a raw public HTTPS workflow .json on GitHub or Hugging Face "
                        "(a model file is never a reference; install it with comfy_install)"]
            response = self.fetch(url, timeout_s=30)
            if not response.ok:
                return [f"Reference workflow fetch failed (HTTP {response.status}); nothing is authorised for install"]
            reference = response.json()
            if not isinstance(reference, dict):
                return ["Reference workflow is not a JSON object"]
        elif path.exists():
            reference = json.loads(path.read_text(encoding="utf-8"))["graph"]
        else:
            return ["This model has separate companion files. Pass reference_workflow_url with the publisher's "
                    "actual workflow JSON so VAE/text encoders are checked before any downloads. "
                    "A search summary or a guessed filename is not evidence."]
        problems = actual.compare(WorkflowModelManifest.from_graph(reference))
        if not problems and url:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_text(json.dumps({"url": url, "graph": reference}), encoding="utf-8")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return problems
