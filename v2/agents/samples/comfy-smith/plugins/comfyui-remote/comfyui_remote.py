"""The agent's private toolkit for a REMOTE ComfyUI.

Seven tools, and the split between them is the point:

  ASK THE SERVER          comfy_server, comfy_nodes, comfy_models
                          Facts about a machine the agent has never seen. Everything here used
                          to be a question put to the user — VRAM, model folder, what's
                          installed — and every one of those questions has a better answer one
                          HTTP call away.

  CHECK ITS OWN WORK      validate_workflow, run_workflow
                          A claim ("this workflow is correct") turned into a fact. run_workflow
                          is the one that closes the loop: write, run, read the real error, fix.

  MOVE THINGS ACROSS      upload_input_image
                          The server is a different computer. A file on this one is invisible
                          to it until it is uploaded.

  REMEMBER                list_workflows

Registered as an agent-private plugin, so no other agent can call them.
"""

from comfy_models_tool import ComfyModelsTool
from comfy_nodes_tool import ComfyNodesTool
from comfy_server_tool import ComfyServerTool
from list_workflows_tool import ListWorkflowsTool
from run_workflow_tool import RunWorkflowTool
from upload_input_image_tool import UploadInputImageTool
from validate_workflow_tool import ValidateWorkflowTool


def register(api, ctx):
    for tool in (
        ComfyServerTool(),
        ComfyNodesTool(),
        ComfyModelsTool(),
        ValidateWorkflowTool(),
        RunWorkflowTool(),
        UploadInputImageTool(),
        ListWorkflowsTool(),
    ):
        api.register_tool(tool)
