"""Fetch and retain a source workflow as evidence, keyed to the designed model stack.

THE EVIDENCE IS A WORKFLOW THAT ALREADY WORKS: the publisher's (a raw GitHub / Hugging Face
.json), or ONE THE USER BROUGHT — an item in their Library (uploaded, saved or a template). A
user's own workflow is evidence for exactly the files it names, the same way a publisher's is: a
model the agent adds that the reference does not name still fails the comparison. What this stops
is the agent inventing companion files; a workflow the person handed over was never that.
"""

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

    def _path(self, manifest: WorkflowModelManifest) -> Path:
        # A changed model stack requires new evidence; sampling/prompt edits can reuse it.
        stack = [sorted(manifest.models), sorted(manifest.vaes), sorted(manifest.encoders)]
        key = hashlib.sha256(json.dumps(stack).encode()).hexdigest()
        return self.root / ".studio" / "model-references" / (key + ".json")

    def remember(self, graph: dict, source: str) -> None:
        """A workflow the USER brought (library_use) is the evidence for its own model stack: kept
        as the reference, so validating it — or a re-emit that keeps its files — needs nothing
        more. A file the agent then adds changes the stack and needs its own evidence again."""
        manifest = WorkflowModelManifest.from_graph(graph)
        if not manifest.needs_reference:
            return
        path = self._path(manifest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"url": source, "graph": graph}), encoding="utf-8")

    def check(self, graph: dict, url: str = "") -> list[str]:
        actual = WorkflowModelManifest.from_graph(graph)
        if not actual.needs_reference:
            return []
        path = self._path(actual)
        # PROVEN ONCE IS PROVEN: a stack with evidence on record (the user's own workflow, or a
        # publisher's checked before) is not un-proven by a different link passed later.
        if path.exists():
            return []
        if url and not url.lower().startswith(("https://", "http://")):
            reference, problem = self._from_library(url)
            if problem:
                return [problem]
        elif url:
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
        if problems and url and not url.lower().startswith(("https://", "http://")):
            problems.append("The user's workflow does not name these files, so they are the agent's "
                            "own additions: use the files the workflow names, or prove the change "
                            "with the publisher's workflow URL.")
        if not problems and url:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_text(json.dumps({"url": url, "graph": reference}), encoding="utf-8")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return problems

    #: Where the user's own workflows live (library_paths): what they uploaded, kept from a chat,
    #: or took from a template. A chat's workflows/ folder is NOT here — the agent writes that.
    LIBRARY = ("library/uploaded/", "library/saved/", "library/suggested/")

    def _from_library(self, rel: str) -> tuple[dict | None, str]:
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel.startswith(self.LIBRARY) or not rel.lower().endswith(".json"):
            return None, ("reference_workflow_url must be a raw GitHub/Hugging Face workflow .json, or "
                          "the path of a workflow .json in the user's Library (library/uploaded/…, "
                          "library/saved/…, library/suggested/…)")
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            return None, f"no Library workflow at {rel} — library_find lists the Library's files"
        try:
            reference = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None, f"{rel} is not valid JSON"
        return (reference, "") if isinstance(reference, dict) else (None, f"{rel} is not a workflow")
