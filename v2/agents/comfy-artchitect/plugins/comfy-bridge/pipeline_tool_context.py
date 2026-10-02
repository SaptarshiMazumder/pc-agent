"""PipelineToolContext — what every design tool works with, assembled once per call.

The Phase 1 tools (kb_lookup, pipeline_plan, stage_set, stage_bind, stage_edit_graph,
pipeline_validate) all need the same collaborators: the knowledge base, the node list of the box's
ComfyUI version, a stage builder, the store for this chat's pipeline and the validators. They are
assembled here from the run's workspace and handed to each tool by its constructor (register()
passes the factory), so no tool reaches for a global and the wiring is in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import studio_state
from family_rule_validator import FamilyRuleValidator
from family_structural_checks import FamilyStructuralChecks
from knowledge_base_catalog import FOLDER as KNOWLEDGE_BASE, KnowledgeBaseCatalog
from node_registry_cache import NodeRegistryCache
from pipeline import Pipeline
from output_size_estimator import OutputSizeEstimator
from pipeline_design_checks import PipelineDesignChecks
from pipeline_store import PipelineStore
from pipeline_validator import PipelineReport, PipelineValidator
from stage_builder import StageBuilder
from task_index import TaskIndex
from workflow_file_writer import WorkflowFileWriter


@dataclass
class PipelineToolContext:
    workspace: Path
    catalog: KnowledgeBaseCatalog
    task_index: TaskIndex | None
    builder: StageBuilder
    store: PipelineStore
    rules: FamilyRuleValidator
    catalogue: dict
    catalogue_source: str
    catalogue_version: str  # the ComfyUI version the node list is OF (may be older than the box)
    comfyui_version: str  # the box's

    @classmethod
    def for_workspace(cls, workspace: Path) -> "PipelineToolContext":
        catalog = KnowledgeBaseCatalog.shipped()
        builder = StageBuilder(catalog)
        version = str((studio_state.read().get("instance") or {}).get("version") or "")
        catalogue, source, listed = NodeRegistryCache(workspace, KNOWLEDGE_BASE).load(version)
        return cls(
            workspace=Path(workspace), catalog=catalog, task_index=TaskIndex.load(KNOWLEDGE_BASE),
            builder=builder, store=PipelineStore(workspace, builder, WorkflowFileWriter(workspace)),
            rules=FamilyRuleValidator(catalog, FamilyStructuralChecks()), catalogue=catalogue,
            catalogue_source=source, catalogue_version=listed, comfyui_version=NodeRegistryCache.version_key(version) or "0.35.0",
        )

    def validate(self, pipeline: Pipeline) -> PipelineReport:
        graphs = {s.name: self.store.graph(s) for s in pipeline.stages}
        return PipelineValidator(self.catalog, self.builder, self.rules, PipelineDesignChecks(self.builder, OutputSizeEstimator(self.builder), self.catalog),
                                 self.catalogue, self.catalogue_source, self.catalogue_version,
                                 self.comfyui_version).check(pipeline, graphs)


__all__ = ["PipelineToolContext"]
