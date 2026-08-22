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

# Safe at module scope: the loader puts this bundle's root on sys.path BEFORE importing this
# module (loader.py — `sys.path.insert(0, root)` precedes `_load_entry_module`). The lazy imports
# inside register() below predate that guarantee.
from agent_authoring.bundle_layout import BundleLayout

log = logging.getLogger("agentd")

# Knowing the product's layout is the composition root's job — but it is the same job for the MCP
# server, so the paths themselves are owned by BundleLayout and named here for readability. The
# services still take both roots as arguments, so a test can point them at a tmp dir.
AGENT_BUILDER_DIR = BundleLayout.AGENT_BUILDER_DIR
TEMPLATE_ROOT = BundleLayout.TEMPLATE_ROOT
BORROW_ROOT = BundleLayout.BORROW_ROOT
COMMON_ROOT = BundleLayout.COMMON_ROOT



def _common_module_sources() -> dict:
    """The canonical text of every shared module, keyed by its path under ``common/``.

    Empty when the templates did not ship with this build — a packaged runtime that carries no
    authoring templates. The rule then has no opinion, which is right: silence beats inventing a
    failure out of our own missing data.
    """
    if not COMMON_ROOT.is_dir():
        return {}
    out = {}
    for path in sorted(COMMON_ROOT.rglob("*")):
        if not path.is_file():
            continue
        try:
            out[path.relative_to(COMMON_ROOT).as_posix()] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # A module we cannot read is one we cannot compare. Skipping is honest; guessing is
            # a false MODIFIED on every agent.
            continue
    return out

def register(api, ctx):
    registry = getattr(ctx, "registry", None)
    if registry is None:
        return  # every tool here is meaningless without the agent roster

    from agent_authoring.application.package_agent_service import PackageAgentService
    from agent_authoring.application.reload_agent_service import ReloadAgentService
    from agent_authoring.application.validate_agent_service import ValidateAgentService
    from agent_authoring.domain.agent_layout_rules import AgentLayoutRules
    from agent_authoring.domain.bundle_defaults import BundleDefaults
    from agent_authoring.domain.declaration_rules import DeclarationRules
    from agent_authoring.domain.common_module_rules import CommonModuleRules
    from agent_authoring.domain.freshness_rules import FreshnessRules
    from agent_authoring.domain.packageability_rules import PackageabilityRules
    from agent_authoring.domain.sandbox_rules import SandboxRules
    from agent_authoring.domain.tool_grant_rules import ToolGrantRules
    from agent_authoring.infrastructure.agent_dir_reader import AgentDirReader
    from agent_authoring.infrastructure.agent_packer import AgentPacker
    from agent_authoring.infrastructure.registry_reload_adapter import RegistryReloadAdapter
    from agent_authoring.presentation.create_agent_tool import CreateAgentTool
    from agent_authoring.presentation.package_agent_tool import PackageAgentTool
    from agent_authoring.presentation.reload_agent_tool import ReloadAgentTool
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
    from agent_runtime.presentation.gateway import APP_SCOPED_METHODS, PROVIDER_ENV_KEYS

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
        # [[settings]] / [[mcp]] / [[oauth]]: the three blocks whose mistakes are invisible
        # until SOMEBODY ELSE has installed the agent — a field nothing reads, a server whose
        # credential was never declared, a key pasted into the file that ships.
        declaration_rules=DeclarationRules(provider_keys=PROVIDER_ENV_KEYS),
        # Is the window that SHIPS built from the source that EXISTS? `app/` is compiled into
        # `ui/`, and only `ui/` is served, packed and published — so an agent whose source has
        # moved on from its build looks finished, validates clean, and hands everyone else the
        # older screen. Now the easy mistake to make: building used to be part of editing, and
        # with build_app it is a separate step somebody has to remember.
        freshness_rules=FreshnessRules(),
        # Is the agent's copy of the shared modules still the shared modules? Read once, at
        # load, because the templates do not change while the daemon runs — and because a
        # rule that reads its own files off disk cannot be tested without staging a tree.
        common_rules=CommonModuleRules(_common_module_sources()),
    )

    # --- AUTHOR ---------------------------------------------------------------------
    # Resolved HERE rather than at the reload_agent registration below, because create_agent
    # needs the same handle: an agent it registers live is invisible to every open window until
    # something announces it, and "the model will call reload_agent next" is not a mechanism.
    broadcast = getattr(ctx, "broadcast_agents_changed", None)
    # The validator rides along so a fresh skeleton is checked IN THE SAME RESULT — the
    # builder sees problems without spending a turn deciding to call validate_agent.
    api.register_tool(CreateAgentTool(registry, validator=validator, announce=broadcast))

    # create_tool hot-loads the Python it writes, so it needs the live-reload handle: without it
    # the tool would write a file that never becomes callable. Register nothing rather than that.
    register_plugin_live = getattr(ctx, "register_plugin_live", None)
    if register_plugin_live is not None:
        from agent_authoring.presentation.create_tool_tool import CreateToolTool

        api.register_tool(CreateToolTool(ctx.config, register_plugin_live, registry))
    else:
        log.info("agent-authoring: no live-reload handle — create_tool not registered")

    # --- PIECES OF AN APP THAT ALREADY EXISTS ----------------------------------------
    # `add_ui_component` IS GONE, with the vanilla templates it existed to patch. It worked by
    # adding a `<script src>` tag to index.html, appending tokens to style.css and splicing a
    # snippet into app.js — three mechanisms a React agent does not have. Against the only kind of
    # window this product now builds it could do half its steps and report success, which is worse
    # than not offering it: the model would call it, believe the piece was installed, and ship an
    # agent without one.
    #
    # THE CATALOGUE IT READ REMAINS, and is not the same thing. `UiComponents` is what tells
    # `validate_agent` which pieces are mandatory and how to recognise them, so it is load-bearing
    # for UI_NO_SIGN_IN and UI_NO_CREDITS. What ended is the PATCHER, not the definition.
    #
    # The fix messages on those findings now say what to write, in React, instead of naming a tool
    # that cannot finish the job.

    # `scaffold_ui` IS DELIBERATELY NOT REGISTERED. It copied a complete vanilla app — plain JS
    # into `ui/`, no build step — and a window is a React project now: source in `app/`, compiled
    # into `ui/` by `build_app`, with the toolchain shipped in the product so there is nothing for
    # a user to install. One way to give an agent a window, so there is no wrong one to pick.
    #
    # The hand-written `ui/` folders a dozen older agents still carry are served straight off disk
    # and keep working; nothing maintains them any more, and rebuilding one means rebuilding it in
    # React. That is deliberate — two UI stacks meant two of everything, and every new capability
    # had to land twice.

    # THE OTHER WAY TO GIVE AN AGENT A WINDOW. `scaffold_ui` copies a finished vanilla app,
    # which is right when a chat window IS the product: no build step, no Node, and a model
    # writing one from scratch reliably gets the event wiring wrong. `scaffold_react_app` copies
    # only a buildable project and deliberately no source — for a window that needs more than a
    # conversation, where there is no single right shape and the working agents under
    # agents/samples/ are the material to judge from.
    from agent_authoring.application.scaffold_react_app_service import ScaffoldReactAppService
    from agent_authoring.presentation.scaffold_react_app_tool import ScaffoldReactAppTool

    api.register_tool(
        ScaffoldReactAppTool(ScaffoldReactAppService(reader, BORROW_ROOT / "react", COMMON_ROOT))
    )

    # AND THE STEP THAT MAKES IT VISIBLE. `app/` is source and `ui/` is what the daemon serves, so
    # an edit that is never built is an edit the user cannot see — they reload the window, get the
    # old screen, and nothing on it explains why. Until this tool existed the only way to run vite
    # was a terminal, which the people who INSTALL this product do not have.
    #
    # The toolchain and the dependency store are separate objects on purpose: one finds and runs
    # Node, the other decides how an app gets its packages (a link to the product's shared copy, or
    # a real install in a source checkout). Both read the environment the supervisor prepared.
    from agent_authoring.application.build_app_service import BuildAppService
    from agent_authoring.infrastructure.app_dependency_store import AppDependencyStore
    from agent_authoring.infrastructure.node_toolchain import NodeToolchain
    from agent_authoring.presentation.build_app_tool import BuildAppTool

    toolchain = NodeToolchain()
    api.register_tool(
        BuildAppTool(BuildAppService(reader, toolchain, AppDependencyStore(toolchain)))
    )

    # VERIFY THE WINDOW. validate_agent proves an agent is well-formed and `agentd ask` proves
    # its brain runs; neither opens the screen, which is the one part that can be perfectly built,
    # perfectly served and blank. The driver is a FACTORY: a browser is expensive and must not be
    # held open between calls. The gateway reader is injected so the daemon token is resolved
    # here and never travels through the model's context.
    from agent_runtime import lifecycle
    from agent_runtime.application.run_context import current_workspace

    from agent_authoring.application.verify_app_service import VerifyAppService
    from agent_authoring.infrastructure.playwright_page_driver import PlaywrightPageDriver
    from agent_authoring.presentation.verify_app_tool import VerifyAppTool

    def _shot_dir():
        # Agent Builder's OWN workspace: the screenshots are evidence for the builder, not files
        # the built agent should ship or a user should find in their agent's folder.
        return Path(current_workspace(".")) / "verify"

    # RUN WHAT IT BUILT. The skill used to send it to the shell for `agentd ask`, which exists
    # only where the wheel was pip-installed — not in a source checkout, which is where agents
    # are authored. A tool cannot be missing from PATH.
    from agent_authoring.presentation.run_agent_tool import RunAgentTool

    api.register_tool(RunAgentTool())

    api.register_tool(
        VerifyAppTool(
            VerifyAppService(
                reader,
                driver_factory=lambda shot: PlaywrightPageDriver(_shot_dir(), want_shot=shot),
                gateway_reader=lifecycle.find_running,
                screenshot_dir=_shot_dir(),
            )
        )
    )

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
    reloader = RegistryReloadAdapter(registry, register_plugin_live, broadcast)
    api.register_tool(ReloadAgentTool(ReloadAgentService(reloader)))

    if broadcast is None:
        log.info("agent-authoring: no broadcast handle — reload_agent will not refresh clients")
