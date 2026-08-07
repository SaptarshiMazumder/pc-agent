"""FileAgentRegistry — discover agents from `agents/<id>/` directories.

"An agent is a directory": each `<agents_dir>/<id>/` holds an optional `agent.toml`
(model, tool allow/deny, skill allowlist, workspace, heartbeat) plus bootstrap
markdown (IDENTITY/AGENTS/USER/MEMORY) read by `load_bootstrap`.

`main` is ALWAYS present and is now a FIRST-CLASS agent like any other: rooted at
`agents/main/` (workspace at `agents/main/workspace/`, skills at `agents/main/skills/`)
and partitioned to `<state_dir>/agents/main/sessions/`. If no `agents/main/` dir exists
it is SYNTHESIZED with those same paths. main's `skills/` is the SHARED/global library
every agent inherits; each named agent's `skills/` is private to it. One bad agent dir
never breaks the rest.
"""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path

from agent_runtime.application.descriptions import first_meaningful_line
from agent_runtime.domain.agent import AgentSpec, agent_id_from_session_key
from agent_runtime.infrastructure.agents.bootstrap import load_bootstrap, load_heartbeat
from agent_runtime.infrastructure.agents.presentation import MAIN_COLOR, read_sidecar

log = logging.getLogger("agentd")


def _valid_id(s: str) -> bool:
    return bool(s) and all(c.isalnum() or c in "-_" for c in s)


# The starter page create() scaffolds for a NEW app agent — self-contained (no SDK, no
# build step) so /apps/<id>/ renders the moment the agent exists; the author replaces it.
_APP_UI_STARTER = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__TITLE__</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; display: grid; place-items: center;
           min-height: 100vh; background: #fafaf7; color: #1a1a1a; }
    main { max-width: 560px; padding: 32px; line-height: 1.55; }
    code { background: #ececec; border-radius: 4px; padding: 1px 5px; font-size: 0.92em; }
  </style>
</head>
<body>
  <main>
    <h1>__TITLE__</h1>
    <p>This is <strong>__ID__</strong>'s app, served by the agentd daemon at
       <code>/apps/__ID__/</code>.</p>
    <p>Build it by editing <code>agents/__ID__/ui/index.html</code>. Talk to the daemon with
       <code>@agentd/client</code> (chat streaming, direct tool invocation, artifacts —
       see docs/PROTOCOL.md).</p>
  </main>
</body>
</html>
"""


def _resolve_agent_description(agent_dir: Path, toml_data: dict, tagline: str) -> str:
    """An agent's one-line description, self-sourced with a fallback chain (never empty for a
    real agent). `agent.toml [description]` wins; else the first prose line of `IDENTITY.md`
    (its H1 is the NAME, so we skip headings); else `bundle.toml`'s marketplace description;
    else the generated tagline. So a client/roster never sees a bare id — no hand-kept field."""
    explicit = str(toml_data.get("description") or "").strip()
    if explicit:
        return explicit
    identity = agent_dir / "IDENTITY.md"
    if identity.is_file():
        try:
            line = first_meaningful_line(identity.read_text(encoding="utf-8"), skip_headings=True)
        except OSError:
            line = ""
        if line:
            return line
    bundle = agent_dir / "bundle.toml"
    if bundle.is_file():
        try:
            b = tomllib.loads(bundle.read_text(encoding="utf-8")).get("bundle") or {}
            if b.get("description"):
                return str(b["description"]).strip()
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return tagline or ""


class FileAgentRegistry:
    """File-backed AgentRegistry. Discovers once at construction (cheap, cached).

    TWO LAYERS, on a hosted daemon. The SHARED layer is whatever the deployment ships in
    ``config.agents_dir`` — a curated catalogue every account sees. The OVERLAY layer is the
    agents ONE account installed for itself (``user_state.account_agents_dir``), resolved per
    connection from the account contextvar.

    Reads union the two with the overlay winning, so installing your own build of a curated agent
    replaces it FOR YOU and for nobody else. Writes (add/create/remove) go to the overlay whenever
    an account is active, which is what stops one visitor's marketplace install from appearing in
    everyone's agent list — and their uninstall from removing it from everyone's.

    ``overlay_dir`` is a CALLABLE, not a path: the answer changes per connection, and the registry
    is constructed once at boot. It returns None on desktop (no accounts), where there is exactly
    one user and the shared layer is the whole truth — so that path is byte-for-byte what it was.
    """

    def __init__(self, config, overlay_dir=None):
        self._config = config
        self._agents_dir = Path(
            getattr(config, "agents_dir", None) or Path(config.state_dir).parent / "agents"
        )
        self._overlay_dir = overlay_dir
        #: str(overlay path) -> that account's specs. Keyed by PATH rather than account id so the
        #: cache cannot outlive a re-pointed root, and so a test can drive it without an account.
        self._overlays: dict[str, dict[str, AgentSpec]] = {}
        self._specs = self._discover()

    def refresh(self) -> list[str]:
        """Re-scan agents/ and swap the cache — how a marketplace install (or any
        out-of-band drop of an agents/<id>/ dir) becomes visible WITHOUT a restart.
        Atomic swap: readers see the old dict or the new one, never a partial."""
        self._specs = self._discover()
        # Overlays are dropped wholesale rather than re-scanned: refresh() runs after an install,
        # the installing account's overlay is the one that changed, and re-scanning every account
        # that ever connected would make one user's install cost work proportional to everyone.
        # They rebuild lazily on next read.
        self._overlays.clear()
        return sorted(self._current())

    # ---- discovery ----------------------------------------------------------

    def _scan(self, directory: Path) -> dict[str, AgentSpec]:
        """Every valid ``<dir>/<id>/`` under one root. No main synthesis — that belongs to the
        shared layer only (an overlay must not invent an agent the account never installed)."""
        specs: dict[str, AgentSpec] = {}
        if not directory.is_dir():
            return specs
        for d in sorted(directory.iterdir()):
            if not d.is_dir():
                continue
            agent_id = d.name.strip().lower()
            if not _valid_id(agent_id):
                log.warning("agents: skipping invalid dir name %r", d.name)
                continue
            try:
                specs[agent_id] = self._load_dir(agent_id, d)
            except Exception as e:  # noqa: BLE001 — one bad agent must not break the rest
                log.warning("agents: failed to load '%s': %s", agent_id, e)
        return specs

    def _discover(self) -> dict[str, AgentSpec]:
        specs = self._scan(self._agents_dir)
        if "main" not in specs:
            specs["main"] = self._synthesize_main()
        log.info("agents: %d loaded (%s)", len(specs), ", ".join(sorted(specs)))
        return specs

    # ---- the two layers -----------------------------------------------------

    def _overlay_path(self) -> Path | None:
        if self._overlay_dir is None:
            return None
        try:
            path = self._overlay_dir()
        except Exception:  # noqa: BLE001 — a broken resolver must degrade to the shared catalogue,
            log.exception("agents: overlay resolver failed — using the shared catalogue only")
            return None
        return Path(path) if path else None

    def _overlay(self) -> dict[str, AgentSpec]:
        path = self._overlay_path()
        if path is None:
            return {}
        key = str(path)
        cached = self._overlays.get(key)
        if cached is None:
            cached = self._scan(path)
            self._overlays[key] = cached
        return cached

    def _current(self) -> dict[str, AgentSpec]:
        """What THIS caller may see: shared catalogue + their own installs."""
        overlay = self._overlay()
        return {**self._specs, **overlay} if overlay else self._specs

    def _write_target(self) -> tuple[dict[str, AgentSpec], Path]:
        """Where a NEW agent goes: the caller's overlay when they have one, else shared."""
        path = self._overlay_path()
        if path is None:
            return self._specs, self._agents_dir
        return self._overlay(), path

    def _main_display_name(self) -> str:
        # main's USER-FACING name — the internal id "main" must never surface in a client.
        # config.agent_name seeds it (AGENTD_AGENT_NAME-overridable); an authored
        # agents/main/agent.toml `name` wins where present (_load_dir).
        return getattr(self._config, "agent_name", "") or "the assistant"

    def _synthesize_main(self) -> AgentSpec:
        # main is a FIRST-CLASS agent rooted at agents/main/ (no agents/main/ dir on disk
        # yet -> use the same paths it WOULD load from, so behaviour is identical once the
        # dir exists). main's skills are the SHARED/global library every agent inherits.
        d = self._agents_dir / "main"
        workspace = d / "workspace"
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return AgentSpec(
            id="main",
            name=self._main_display_name(),
            description="general · all tools",  # the generalist: no specialty to advertise
            tagline="general · all tools",  # honest default for the generalist
            color=MAIN_COLOR,  # the brand lime — reserved for main
            workspace=workspace,
            state_dir=self._state_dir_for("main"),
            instructions="",
            model=None,
            tools_allow=None,
            tools_deny=(),
            skills_allow=None,
            skills_dir=d / "skills",  # main's skills = the global library
            dir=d,
        )

    def _state_dir_for(self, agent_id: str) -> Path:
        # every agent (main included) partitions to <state_dir>/agents/<id>/.
        return Path(self._config.state_dir) / "agents" / agent_id

    def _load_dir(self, agent_id: str, d: Path) -> AgentSpec:
        data: dict = {}
        toml_path = d / "agent.toml"
        if toml_path.is_file():
            with toml_path.open("rb") as f:
                data = tomllib.load(f)

        ws = data.get("workspace")
        if ws:
            workspace = Path(ws).expanduser()  # explicit path wins
        else:
            # EVERY agent (main included) gets its OWN isolated workspace at
            # agents/<id>/workspace/ (created on demand), so files never collide.
            workspace = d / "workspace"
            try:
                workspace.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

        tools = data.get("tools") or {}
        allow = tools.get("allow")
        deny = tools.get("deny") or []
        skills_allow = data.get("skills")
        model = data.get("model")
        # [plugins.*] per-agent model overrides, same plugin->tool->model shape as global
        # config.plugins. e.g.  [plugins.vision]  model = "..."  /  [plugins.vision.tools.verify_figure]
        # model = "...". Layered ABOVE global config.plugins by resolve_tool_model. Lowercase the
        # plugin keys so lookups (which lowercase the plugin) match; leave the nested body intact.
        plugins = {
            str(k).lower(): v for k, v in (data.get("plugins") or {}).items() if isinstance(v, dict)
        }
        heartbeat = data.get("heartbeat")

        # [subagents] allow — which specialist agents this one may delegate to (ids/globs).
        subagents = data.get("subagents") or {}
        sub_allow = subagents.get("allow")

        # [capabilities] gates the "What you are" self-knowledge. Absent key => None =>
        # inherit the global default; an explicit true/false overrides it for this agent.
        caps = data.get("capabilities") or {}

        # [safe_to_send] audience = "external" => apply the privacy gate to this agent's channel
        # replies. Absent / "internal" / anything else => the gate is NOT applied to this agent.
        sts = data.get("safe_to_send") or {}
        audience = str(sts.get("audience") or "").strip().lower()

        # [app] — this agent ships its own client UI (docs/PROTOCOL.md §9). Pure declaration:
        # entry is a path RELATIVE to the agent dir (default ui/index.html); the gateway
        # validates existence + serves it. Absent section => a plain chat agent (app=None).
        app_raw = data.get("app")
        app = None
        if isinstance(app_raw, dict):
            mode = str(app_raw.get("mode") or "browser").strip().lower()
            app = {
                "entry": str(app_raw.get("entry") or "ui/index.html"),
                "title": str(app_raw.get("title") or data.get("name") or agent_id),
                # how openers PRESENT the app — its own chromeless "window" (program
                # feel) or a normal "browser" tab. The AUTHOR declares it; every opener
                # honors it. Unknown values fall back to browser.
                "mode": mode if mode in ("window", "browser") else "browser",
                # PUBLIC access (hosted deployments): public = true lets UNAUTHENTICATED
                # connections scoped to this agent in; public_tools is the ONLY tool
                # subset such visitors may invoke (the agent's own allow/deny still
                # applies on top). Absent => private, () => public app with no tools.
                "public": bool(app_raw.get("public", False)),
                "public_tools": tuple(
                    str(t).strip()
                    for t in (app_raw.get("public_tools") or [])
                    if str(t).strip()
                ),
            }

        # Display presentation: authored agent.toml fields win; else the sidecar the
        # daemon generated once from the identity (presentation.json). main is the
        # generalist BY DEFINITION — it gets the standard line rather than a guess.
        sidecar = read_sidecar(d)
        tagline = str(
            data.get("tagline")
            or sidecar.get("tagline")
            or ("general · all tools" if agent_id == "main" else "")
        )
        suggestions = tuple(
            str(s).strip()
            for s in (data.get("suggestions") or sidecar.get("suggestions") or [])
            if str(s).strip()
        )[:3]
        # colour: authored agent.toml wins; main is pinned to brand lime (never the
        # assigned sidecar); other agents use their assigned sidecar colour, else "".
        if agent_id == "main":
            color = str(data.get("color") or MAIN_COLOR)
        else:
            color = str(data.get("color") or sidecar.get("color") or "")

        return AgentSpec(
            id=agent_id,
            # main's fallback is its display name, NOT the internal id — the moment
            # agents/main/ materialises on disk (workspace/skills are auto-created) it
            # loads through here, and "main" must never become the user-facing name.
            name=str(
                data.get("name") or (self._main_display_name() if agent_id == "main" else agent_id)
            ),
            description=_resolve_agent_description(d, data, tagline),
            tagline=tagline,
            suggestions=suggestions,
            color=color,
            workspace=workspace,
            state_dir=self._state_dir_for(agent_id),
            instructions=load_bootstrap(d),
            model=str(model) if model else None,
            plugins=plugins,
            tools_allow=tuple(allow) if allow is not None else None,
            tools_deny=tuple(deny),
            subagents_allow=tuple(sub_allow) if sub_allow is not None else None,
            dir=d,
            skills_allow=tuple(skills_allow) if skills_allow is not None else None,
            skills_dir=d / "skills",  # the agent's OWN skills (agents/<id>/skills/)
            google_account=str(data.get("google_account") or ""),
            google_accounts=tuple(str(a) for a in (data.get("google_accounts") or [])),
            audience=audience,
            autonomy_enabled=caps.get("autonomy"),
            notify_enabled=caps.get("notify"),
            channels_enabled=caps.get("channels"),
            heartbeat=str(heartbeat) if heartbeat else None,
            heartbeat_instructions=load_heartbeat(d),
            version=str(data.get("version") or "1"),
            app=app,
        )

    @property
    def agents_dir(self) -> Path:
        """The root an authoring tool should WRITE a new agent into — the caller's overlay when
        they have one, else the shared catalogue. Discovery reads from both, so this stays the
        "same place discovery reads from" it always was; on a hosted daemon it just stops meaning
        "the place everyone reads from"."""
        return self._write_target()[1]

    def add(self, agent_id: str) -> AgentSpec:
        """(Re)load ONE ``agents/<id>/`` dir into the registry at runtime, so a newly-authored
        agent is resolvable WITHOUT a restart — the inverse of ``remove()``. ``resolve``/``get``
        read the live maps each turn, so the new agent is usable on the next message.

        Looks in the caller's overlay FIRST: a marketplace install lands there, and finding the
        shared copy of the same id instead would load the curated agent while the user's own
        install sat on disk doing nothing.
        """
        agent_id = (agent_id or "").strip().lower()
        if not _valid_id(agent_id):
            raise ValueError(f"invalid agent id: {agent_id!r}")
        overlay_path = self._overlay_path()
        for specs, root in (
            (self._overlay(), overlay_path) if overlay_path is not None else (None, None),
            (self._specs, self._agents_dir),
        ):
            if specs is None or root is None:
                continue
            d = root / agent_id
            if d.is_dir():
                spec = self._load_dir(agent_id, d)
                specs[agent_id] = spec
                log.info("agents: added '%s' at runtime from %s", agent_id, root)
                return spec
        raise FileNotFoundError(str((overlay_path or self._agents_dir) / agent_id))

    def create(
        self,
        agent_id: str,
        name: str = "",
        description: str = "",
        identity: str = "",
        app: str = "",
    ) -> AgentSpec:
        """Scaffold a NEW agent definition and load it live (no restart) — the inverse
        of remove(). Writes agents/<id>/agent.toml (name + description) and, if given,
        IDENTITY.md (who the agent is, read into its bootstrap). ``app`` makes it an
        APP AGENT (docs/PROTOCOL.md §9): "browser" or "window" declares how openers
        present its UI, and a self-contained starter page is scaffolded into ui/ so the
        app is openable the moment it exists. Refuses an invalid id or one that already
        exists. Colour + tagline are filled in afterwards by the daemon's presentation
        pass. Returns the loaded spec."""
        agent_id = (agent_id or "").strip().lower()
        if not _valid_id(agent_id):
            raise ValueError(f"invalid agent id: {agent_id!r} (use letters, digits, - or _)")
        target_specs, target_root = self._write_target()
        d = target_root / agent_id
        # Collides against what this caller can SEE (shared + their overlay), not just the layer
        # being written to: creating an agent whose id shadows a curated one would look like it
        # worked and then resolve to the wrong definition on the next message.
        if agent_id in self._current() or d.exists():
            raise ValueError(f"agent '{agent_id}' already exists")

        d.mkdir(parents=True, exist_ok=True)
        (d / "workspace").mkdir(exist_ok=True)
        # agent.toml — JSON string literals are valid TOML basic strings, so this
        # safely escapes quotes/backslashes in the name/description.
        lines = [f"name = {json.dumps(name or agent_id)}"]
        if description.strip():
            lines.append(f"description = {json.dumps(description.strip())}")
        app = (app or "").strip().lower()
        if app:
            mode = app if app in ("browser", "window") else "browser"
            title = name or agent_id
            lines += ["", "[app]", f"title = {json.dumps(title)}", f"mode = {json.dumps(mode)}"]
            ui = d / "ui"
            ui.mkdir(exist_ok=True)
            (ui / "index.html").write_text(
                _APP_UI_STARTER.replace("__TITLE__", title).replace("__ID__", agent_id),
                encoding="utf-8",
            )
        (d / "agent.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if identity.strip():
            (d / "IDENTITY.md").write_text(identity.strip() + "\n", encoding="utf-8")

        spec = self._load_dir(agent_id, d)
        target_specs[agent_id] = spec
        log.info("agents: created '%s' (%s) in %s", agent_id, name or agent_id, target_root)
        return spec

    # ---- AgentRegistry ------------------------------------------------------

    def resolve(self, session_key: str) -> AgentSpec:
        specs = self._current()
        return specs.get(agent_id_from_session_key(session_key)) or specs["main"]

    def get(self, agent_id: str) -> AgentSpec:
        return self._current()[agent_id]

    def list_ids(self) -> list[str]:
        return sorted(self._current())

    def remove(self, agent_id: str) -> dict:
        """Delete an agent's DEFINITION dir (agent.toml/IDENTITY/… + its workspace) and
        its sessions dir, and forget it in-memory so no restart is needed. Refuses
        `main` (always-present default). The shared sqlite ledgers (memory/autonomy) are
        purged by their stores, not here. Returns what was removed.
        """
        import shutil

        agent_id = (agent_id or "").strip().lower()
        if agent_id == "main":
            raise ValueError("cannot delete the default agent 'main'")

        overlay_path = self._overlay_path()
        if overlay_path is not None:
            # THE ISOLATION RULE. An account may delete only what it installed. Without this an
            # ordinary uninstall of a CURATED agent would rmtree the shared catalogue — one user
            # removing an agent from every other user's account, permanently, with the UI
            # reporting success. That is the single most destructive thing per-account installs
            # could get wrong, so it is refused here rather than anywhere further out.
            overlay = self._overlay()
            if agent_id not in overlay:
                if agent_id in self._specs:
                    raise ValueError(
                        f"'{agent_id}' is part of this deployment's shared catalogue and cannot "
                        "be removed by an account — uninstall only affects agents you installed"
                    )
                raise KeyError(agent_id)
            specs, root = overlay, overlay_path
        else:
            if agent_id not in self._specs:
                raise KeyError(agent_id)
            specs, root = self._specs, self._agents_dir

        removed = {"id": agent_id, "definition": False, "sessions": False}
        def_dir = root / agent_id  # definition + workspace/ live here
        if def_dir.is_dir():
            shutil.rmtree(def_dir, ignore_errors=True)
            removed["definition"] = not def_dir.exists()
        state_dir = Path(self._state_dir_for(agent_id))  # <state_dir>/agents/<id>/ (sessions)
        if state_dir.is_dir():
            shutil.rmtree(state_dir, ignore_errors=True)
            removed["sessions"] = not state_dir.exists()
        # NOTE (accounts): this is the SHARED sessions path. An account's transcripts live under
        # <state_dir>/accounts/<acct>/agents/<id>/ (user_state.account_state_dir), so uninstalling
        # leaves them on disk. Deliberate for now — orphaned data is recoverable and losing a
        # user's history to an uninstall is not — but it means uninstall+reinstall resurrects the
        # old chats. Wire account_state_dir in here when uninstall grows a "delete my data" flag.
        del specs[agent_id]
        log.info(
            "agents: removed '%s' (definition=%s sessions=%s)",
            agent_id,
            removed["definition"],
            removed["sessions"],
        )
        return removed
