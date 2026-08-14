"""agent-authoring — the bundle's COMPOSITION ROOT.

The only place in this bundle that wires anything: it reads the handles off PluginContext, builds
the rules -> services -> adapters -> tools chain, and registers the four tools. Every class below
is constructor-injected, so each is unit-testable without a daemon.

Layout note: the layered code lives under the ``agent_authoring/`` package rather than bare
``domain/`` + ``application/`` folders, because the loader does ``sys.path.insert(0, <plugin dir>)``
for EVERY plugin — generic top-level package names would collide between bundles. One uniquely
named package keeps the layering visible and the namespace safe.

All four tools are ungated. The two that AUTHOR (create_agent, create_tool) used to sit behind
the agent_workshop / tool_workshop config flags; they no longer need to, because this bundle is
PRIVATE to the agent-builder agent — only that agent can call them, which is the boundary those
flags were approximating from a distance.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("agentd")

# agents/agent-builder/ — this file sits at <that>/plugins/agent-authoring/. Knowing the
# product's layout is the composition root's job; the service below takes the two roots as
# arguments so it can be pointed at a tmp dir in a test.
AGENT_BUILDER_DIR = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = AGENT_BUILDER_DIR / "skills" / "build-agent" / "templates"
BORROW_ROOT = AGENT_BUILDER_DIR / "ui"


def register(api, ctx):
    registry = getattr(ctx, "registry", None)
    if registry is None:
        return  # every tool here is meaningless without the agent roster

    from agent_authoring.application.package_agent_service import PackageAgentService
    from agent_authoring.application.reload_agent_service import ReloadAgentService
    from agent_authoring.application.scaffold_ui_service import ScaffoldUiService
    from agent_authoring.application.validate_agent_service import ValidateAgentService
    from agent_authoring.domain.agent_layout_rules import AgentLayoutRules
    from agent_authoring.domain.bundle_defaults import BundleDefaults
    from agent_authoring.domain.packageability_rules import PackageabilityRules
    from agent_authoring.domain.sandbox_rules import SandboxRules
    from agent_authoring.domain.tool_grant_rules import ToolGrantRules
    from agent_authoring.domain.ui_template import UiTemplates
    from agent_authoring.infrastructure.agent_dir_reader import AgentDirReader
    from agent_authoring.infrastructure.agent_packer import AgentPacker
    from agent_authoring.infrastructure.registry_reload_adapter import RegistryReloadAdapter
    from agent_authoring.presentation.create_agent_tool import CreateAgentTool
    from agent_authoring.presentation.package_agent_tool import PackageAgentTool
    from agent_authoring.presentation.reload_agent_tool import ReloadAgentTool
    from agent_authoring.presentation.scaffold_ui_tool import ScaffoldUiTool
    from agent_authoring.presentation.validate_agent_tool import ValidateAgentTool

    # --- CHECK, BUILT FIRST — the author tools gate on it -----------------------------
    # The UI rules are told the real vocabulary rather than keeping their own copy: event
    # names from the runtime's domain, callable methods from the gateway's app tier. A second
    # copy would be one more thing to drift — which is the exact class of bug they exist to
    # catch (a skill that listed an event nobody emits produced a UI with every branch dead).
    #
    # ONE validator instance serves create_agent (auto-check on birth), validate_agent,
    # package_agent and publish_agent — and severity/gate POLICY for every rule lives in ONE
    # table (domain/rulebook.py), so what each of those refuses on can never diverge.
    from agent_authoring.domain.portability_rules import PortabilityRules
    from agent_authoring.domain.ui_component import UiComponents
    from agent_authoring.domain.ui_rules import UiRules

    from agent_runtime.domain.events import APP_FACING_EVENTS, MESSAGE_UPDATE_KINDS
    from agent_runtime.presentation.gateway import APP_SCOPED_METHODS

    reader = AgentDirReader(registry)
    components = UiComponents()
    validator = ValidateAgentService(
        reader,
        AgentLayoutRules(),
        PackageabilityRules(),
        SandboxRules(),
        UiRules(
            events=APP_FACING_EVENTS,
            kinds=MESSAGE_UPDATE_KINDS,
            methods=frozenset(APP_SCOPED_METHODS),
            sdk_methods=frozenset(),  # populated from the vendored SDK once that is parsed
            # The SAME catalogue the component tool inserts from. So "is sign-in present?" and
            # "what does adding sign-in write?" are one definition — three unshared copies of
            # that snippet is what this replaces.
            components=components.all(),
        ),
        # Half a tool pair: `exec` without `process` leaves the model unable to poll a
        # background job, so it blocks a turn on a sleep instead. Caught here rather than
        # discovered during a 20GB download.
        ToolGrantRules(),
        # Declarations that contradict where the agent is GOING: hosted (no shell, fenced
        # reads, empty per-user workspace) and buyers' installs (clamped write scope).
        PortabilityRules(),
    )

    # --- AUTHOR ---------------------------------------------------------------------
    # The validator rides along so a fresh skeleton is checked IN THE SAME RESULT — the
    # builder sees problems without spending a turn deciding to call validate_agent.
    api.register_tool(CreateAgentTool(registry, validator=validator))

    # create_tool hot-loads the Python it writes, so it needs the live-reload handle: without it
    # the tool would write a file that never becomes callable. Register nothing rather than that.
    register_plugin_live = getattr(ctx, "register_plugin_live", None)
    if register_plugin_live is not None:
        from agent_authoring.presentation.create_tool_tool import CreateToolTool

        api.register_tool(CreateToolTool(ctx.config, register_plugin_live, registry))
    else:
        log.info("agent-authoring: no live-reload handle — create_tool not registered")

    # --- START THE APP FROM SOMETHING THAT WORKS -------------------------------------
    # Registered next to create_agent because it is the same kind of step: create_agent makes
    # the agent exist, scaffold_ui makes its window exist. Both hand back a working starting
    # point and say what to do next; neither tries to author the whole thing.
    #
    # TWO TIERS OF REUSE, and they are different operations rather than one with a flag:
    #   templates  a WHOLE app, copied. Refuses over an existing ui/ — that is somebody's work.
    #   components a PIECE, patched into an app that already exists. Idempotent, so it can be
    #              applied whenever the model is unsure.
    # ONE AddComponentService instance serves both, which is what stops "scaffold with sign-in"
    # and "add sign-in later" from becoming two definitions of the same snippet.
    from agent_authoring.application.add_component_service import AddComponentService
    from agent_authoring.presentation.add_ui_component_tool import AddUiComponentTool

    templates = UiTemplates()
    component_service = AddComponentService(
        reader, components, TEMPLATE_ROOT / "components", BORROW_ROOT
    )
    api.register_tool(
        ScaffoldUiTool(
            ScaffoldUiService(
                reader, templates, TEMPLATE_ROOT, BORROW_ROOT, components=component_service
            ),
            templates,
            components,
        )
    )
    api.register_tool(AddUiComponentTool(component_service, components))

    # --- CHECK (the tool face of the validator built above) ---------------------------
    api.register_tool(ValidateAgentTool(validator))

    # --- SHIP -----------------------------------------------------------------------
    # The SAME validator instance the tool exposes — packaging gates on it, so "what
    # validate_agent told you" and "what package_agent refuses on" can never diverge.
    api.register_tool(
        PackageAgentTool(
            PackageAgentService(
                reader,
                AgentPacker(ctx.config),
                BundleDefaults(),
                validator,
            )
        )
    )

    # Publishing is the same artifact going one step further — to the registry other people
    # install from. It reuses the CLI's publisher (guards and all) rather than owning a second
    # one, gates on the SAME validator instance, and previews unless told twice to upload.
    from agent_authoring.presentation.publish_agent_tool import PublishAgentTool

    api.register_tool(PublishAgentTool(ctx.config, registry, validator))

    # --- ACTIVATE -------------------------------------------------------------------
    # register_plugin_live picks up NEW agents/<id>/plugins/; broadcast_agents_changed refreshes
    # every client's sidebar. Both are OPTIONAL — reload still does what it can without them,
    # and reports honestly which steps it managed.
    broadcast = getattr(ctx, "broadcast_agents_changed", None)
    reloader = RegistryReloadAdapter(registry, register_plugin_live, broadcast)
    api.register_tool(ReloadAgentTool(ReloadAgentService(reloader)))

    if broadcast is None:
        log.info("agent-authoring: no broadcast handle — reload_agent will not refresh clients")
