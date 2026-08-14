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
from agent_runtime.domain import ownership
from agent_runtime.domain.agent import USER_DATA_DIRS, AgentSpec, agent_id_from_session_key
from agent_runtime.domain.agent_availability import withheld_reason
from agent_runtime.infrastructure.agents import ownership_store
from agent_runtime.infrastructure.agents.bootstrap import load_bootstrap, load_heartbeat
from agent_runtime.infrastructure.agents.presentation import MAIN_COLOR, read_sidecar

log = logging.getLogger("agentd")


def _valid_id(s: str) -> bool:
    return bool(s) and all(c.isalnum() or c in "-_" for c in s)


def is_definition_dir(d: Path) -> bool:
    """Is this directory an agent DEFINITION, or just some agent's data?

    The declaration is `agent.toml`. It is the whole test, and it has to be: a folder now holds
    an agent's definition next to its `sessions/` and `workspace/`, so "there is a directory
    named <id>" stopped meaning "there is an agent <id> here" the moment a user chatted with an
    agent they do not own a copy of."""
    return (d / "agent.toml").is_file()


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
    is constructed once at boot. It returns None when the CONNECTION has no account (a signed-out
    desktop socket, the CLI) — there the shared layer is the whole truth and that path is
    byte-for-byte what it was. A signed-in desktop connection has an account like any other, and
    with it an overlay; what it never loses is ownership of the shared layer (see ``owns``).
    """

    def __init__(self, config, overlay_dir=None, account_id=None):
        self._config = config
        self._agents_dir = Path(
            getattr(config, "agents_dir", None) or Path(config.state_dir).parent / "agents"
        )
        self._overlay_dir = overlay_dir
        #: () -> the CURRENT connection's account id, or None — same per-connection shape as
        #: overlay_dir, and for the same reason: the registry is built once, the answer isn't.
        #: Ownership needs the id itself (records name accounts), not just the overlay path.
        self._account_id = account_id
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

    def _hosted(self) -> bool:
        return bool(getattr(self._config, "hosted", False))

    def _shared_owner_default(self) -> str:
        """What a record-less SHARED dir is presumed to be: the deployment's own."""
        return ownership.presumed_owner(in_overlay=False, account_id=None, hosted=self._hosted())

    def _with_ownership(self, spec: AgentSpec, d: Path, default_owner: str) -> AgentSpec:
        """Attach who this agent belongs to, resolved ONCE here so every later question —
        visibility, `mine`, provenance — is a field read, never disk IO on a hot path. The
        record (`.agentd-meta.json`) answers when the dir has one; otherwise the layer's
        presumed owner (domain/ownership.py) keeps legacy dirs meaning what they always meant."""
        import dataclasses

        record = ownership_store.read(d)
        return dataclasses.replace(
            spec,
            owner=record.owner if record else default_owner,
            origin=record.origin if record else ownership.AUTHORED,
        )

    def _scan(self, directory: Path, default_owner: str = "") -> dict[str, AgentSpec]:
        """Every valid ``<dir>/<id>/`` under one root. No main synthesis — that belongs to the
        shared layer only (an overlay must not invent an agent the account never installed)."""
        specs: dict[str, AgentSpec] = {}
        if not directory.is_dir():
            return specs
        default_owner = default_owner or self._shared_owner_default()
        for d in sorted(directory.iterdir()):
            if not d.is_dir():
                continue
            agent_id = d.name.strip().lower()
            if not _valid_id(agent_id):
                log.warning("agents: skipping invalid dir name %r", d.name)
                continue
            # `main` is the ONE agent allowed to exist undeclared — `agents/main/` may hold just
            # an IDENTITY.md and the global skills library — but ONLY in the deployment's own
            # catalogue, which is the same rule this scan already applies to main synthesis: an
            # overlay must not invent an agent the account never installed. Allowing it in an
            # overlay would let the data folder of anyone who ever chatted with main shadow the
            # real one, IDENTITY and skills included.
            undeclared_main = agent_id == "main" and directory == self._agents_dir
            if not is_definition_dir(d) and not undeclared_main:
                # AN AGENT DECLARES ITSELF. Now that one folder holds an agent's definition AND
                # its data, the account's agents root also contains a folder for every agent the
                # user has merely CHATTED with — holding sessions/ and workspace/ and nothing
                # else. Loading those as agents made each one an empty spec that SHADOWED the
                # real definition in the layer below: agent-builder lost its [app] and its
                # window, and the shared catalogue effectively went blank for that account.
                # `main` is the one agent that may exist without a declaration, and it has its
                # own path (_synthesize_main), so requiring the file here costs nothing.
                #
                # SAY SO when the directory holds more than the user's own data: an agent that
                # silently fails to appear reads as a broken install, and a half-authored agent
                # (skills but no agent.toml — it happens when authoring is interrupted) is worth
                # one line to whoever wonders where it went. Pure data folders stay silent;
                # there is one per agent anybody has ever chatted with.
                try:
                    residue = [i.name for i in d.iterdir() if i.name not in USER_DATA_DIRS]
                except OSError:
                    residue = []
                if residue:
                    log.warning(
                        "agents: '%s' has no agent.toml, so it is not an agent — skipping %s "
                        "(it still holds %s)",
                        agent_id,
                        d,
                        ", ".join(sorted(residue)[:5]),
                    )
                continue
            try:
                spec = self._with_ownership(self._load_dir(agent_id, d), d, default_owner)
            except Exception as e:  # noqa: BLE001 — one bad agent must not break the rest
                log.warning("agents: failed to load '%s': %s", agent_id, e)
                continue
            # WITHHOLD, at the only choke point there is. An agent absent from the registry has
            # no roster entry, no session key, no app to serve and no private tools discovered —
            # rather than being present everywhere and refused at each surface, which is a list
            # of surfaces somebody eventually forgets to extend. Applied to BOTH layers (shared
            # and a per-account overlay), so installing a local-only agent from the marketplace
            # does not smuggle it onto a hosted daemon either.
            reason = withheld_reason(agent_id, spec.requires_local, self._config)
            if reason:
                # LOUD. An agent that quietly fails to appear looks like a broken install, and
                # the operator who set the policy is rarely the person looking at the sidebar.
                log.info("agents: withholding '%s' on this hosted daemon — %s", agent_id, reason)
                continue
            specs[agent_id] = spec
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
            # A record-less overlay dir is presumed its ACCOUNT's — the dir exists only because
            # that account put something there. Resolved at scan time, which is safe precisely
            # because overlay dirs are per-account: the caller triggering this lazy scan is the
            # account the path belongs to.
            acct = self._account_id() if callable(self._account_id) else None
            default = ownership.presumed_owner(True, acct, self._hosted())
            cached = self._scan(path, default_owner=default)
            self._overlays[key] = cached
        return cached

    def _current(self) -> dict[str, AgentSpec]:
        """What THIS caller may see: their own installs + the agents that are THEIRS to see.

        Desktop: one person, one machine — everything, exactly as always. Hosted: the shared
        layer is DEFAULT-PRIVATE — an account sees a shared agent only when it is the
        platform's (curated) or their own; whatever else sits in that directory (a migrated
        desktop dir, another account's stray copy, operator debris) is invisible, not merely
        refused. The operator (machine token, no account) still sees everything — it is theirs.
        Same choke-point principle as `withheld_reason`: absent from this dict means no roster
        entry, no session key, no app served, nothing for any surface to get wrong."""
        overlay = self._overlay()
        specs = {**self._specs, **overlay} if overlay else self._specs
        if not self._hosted():
            return specs
        acct = self._account_id() if callable(self._account_id) else None
        if not acct:
            return specs
        return {
            aid: s
            for aid, s in specs.items()
            # empty owner = a spec injected without a scan (tests, exotic registries): unrestricted
            if aid in overlay or not s.owner or s.owner in (ownership.PLATFORM_OWNER, acct)
        }

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
            # The deployment's own default agent: the platform's on hosted (visible to every
            # account, publishable by nobody), the local owner's on a desktop — same presumption
            # as any record-less shared dir, stated here because main skips the scan.
            owner=self._shared_owner_default(),
            origin=ownership.CURATED if self._hosted() else ownership.AUTHORED,
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
        # [tools.fs] — where this agent may WRITE. Absent (the normal case) leaves both empty,
        # which means unrestricted, which is what every agent does today. Only an agent that
        # opts in is constrained. Tokens like <agents_dir> are left verbatim; the service that
        # builds the run context expands them, because that is where config lives.
        fs_scope = tools.get("fs") or {}
        write_roots = [str(r).strip() for r in (fs_scope.get("write_roots") or []) if str(r).strip()]
        write_denies = [str(r).strip() for r in (fs_scope.get("deny") or []) if str(r).strip()]
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
            write_roots=tuple(write_roots),
            write_denies=tuple(write_denies),
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
            requires_local=bool(data.get("requires_local")),
        )

    @property
    def agents_dir(self) -> Path:
        """The root an authoring tool should WRITE a new agent into — the caller's overlay when
        they have one, else the shared catalogue. Discovery reads from both, so this stays the
        "same place discovery reads from" it always was; on a hosted daemon it just stops meaning
        "the place everyone reads from".

        This answers "where do NEW agents go", nothing else. To find an agent that already
        exists, use ``resolve_dir`` — building ``agents_dir / id`` finds only the caller's own
        layer, which is how publish once reported an agent everyone could see as nonexistent."""
        return self._write_target()[1]

    @property
    def shared_agents_dir(self) -> Path:
        """The DEPLOYMENT's catalogue root, never the caller's overlay. For work that is
        process-global — private-plugin discovery rebuilds ONE service-wide tool map — reading
        `agents_dir` (the write target) from inside an account-scoped call re-scanned only that
        account's overlay and wholesale-replaced the map with it: creating one agent while
        signed in silently removed agent-builder's publish_agent for the whole daemon."""
        return self._agents_dir

    def overlay_path(self) -> Path | None:
        """The CURRENT connection's overlay root, or None — public for callers that must scan
        BOTH layers explicitly (see `shared_agents_dir`) instead of whichever one `agents_dir`
        happens to mean right now."""
        return self._overlay_path()

    def agent_roots(self) -> tuple[Path, ...]:
        """EVERY root where THIS caller's agents may legitimately live: the shared catalogue,
        plus their account overlay when the connection has one.

        This is THE answer write-scope expansion consumes (AgentService._expand_paths). It
        lives here because placement already does: `agents_dir` (the write target) is always
        one of these roots, so a scope built from this tuple can never disagree with where
        `create_from` puts an agent — the disagreement that deadlocked the agent-builder when
        the scope was derived from static file layout instead of asked of the registry."""
        roots: list[Path] = [self._agents_dir]
        overlay = self._overlay_path()
        if overlay is not None and overlay != self._agents_dir:
            roots.append(overlay)
        return tuple(roots)

    def find_in_account_layers(self, agent_id: str, account_id: str | None = None):
        """One agent's spec from the ACCOUNT layers — the per-REQUEST twin of the
        per-connection overlay. HTTP callers (app serving) are identified per request, not
        per connection, so the caller NAMES the layer(s) instead of a contextvar doing it:
        ``account_id`` searches that account's layer alone; None is the deployment-owner
        view — every account layer on this deployment, first hit in account order.

        Returns None when no named layer has the id. Loads through the same scan+cache as
        the live overlay (``self._overlays``, keyed by layer path), so serving an app does
        not re-read agent.toml per asset request."""
        aid = (agent_id or "").strip().lower()
        if not _valid_id(aid):
            return None
        from agent_runtime.infrastructure import user_state

        state_dir = getattr(self._config, "state_dir", None)
        if not state_dir:
            return None
        candidates = (
            [(account_id, user_state.account_agents_dir(state_dir, account_id))]
            if account_id
            else user_state.account_agents_layers(state_dir)
        )
        for acct, root in candidates:
            root = Path(root)
            if not (root / aid / "agent.toml").is_file():
                continue
            key = str(root)
            cached = self._overlays.get(key)
            if cached is None or aid not in cached:
                # (Re)scan on a miss so an agent created after the cache filled is found —
                # the same freshness rule the live overlay gets from add().
                cached = self._scan(root, default_owner=ownership.presumed_owner(True, acct, self._hosted()))
                self._overlays[key] = cached
            spec = cached.get(aid)
            if spec is not None:
                return spec
        return None

    def resolve_dir(self, agent_id: str) -> Path | None:
        """Where this agent's DEFINITION actually lives, or None — read from the same union
        (and with the same overlay-wins precedence) every other read uses, so "the sidebar
        shows it" and "tools can find it" are one answer, not two."""
        spec = self._current().get((agent_id or "").strip().lower())
        d = getattr(spec, "dir", None) if spec is not None else None
        return Path(d) if d else None

    def _ownership(self, agent_id: str):
        """(owner, origin) for one agent, or None when it doesn't exist for this caller.

        Field reads: the answer was resolved at scan time (`_with_ownership` — the record when
        the dir has one, else the layer's presumed owner), so hot paths never touch disk. See
        planning/platform/identity-ownership-statelessness.md, Step 2."""
        aid = (agent_id or "").strip().lower()
        spec = self._overlay().get(aid) or self._specs.get(aid)
        if spec is None:
            return None
        owner = getattr(spec, "owner", "") or self._shared_owner_default()
        return owner, getattr(spec, "origin", "") or ownership.AUTHORED

    def owns(self, agent_id: str) -> bool:
        """Is this agent the CALLER's — is its owner among the identities they act as?

        On a desktop the human is the operator, so "local" stays theirs even while signed in —
        signing in adds an identity, it never subtracts one (the game-master regression: a
        signed-in author suddenly unable to touch the agents on their own machine). On a hosted
        daemon a stranger acts only as their account, and curated agents stay the platform's."""
        info = self._ownership(agent_id)
        if info is None:
            return False
        acct = self._account_id() if callable(self._account_id) else None
        return ownership.owned_by_caller(info[0], acct, self._hosted())

    def listed(self, agent_id: str) -> bool:
        """Does this agent belong in the CALLER's sidebar/dropdowns?

        Distinct from VISIBLE (`_current`) on purpose: a web-app copy must stay resolvable —
        its /apps/<id> URL, app-scoped connections and session keys all resolve through the
        registry — while appearing in nobody's list uninvited. Everything else listed follows
        visibility exactly. Installing a web app from the Store makes a copy in your overlay,
        and THAT copy lists like any install."""
        info = self._ownership(agent_id)
        if info is None:
            return False
        return info[1] != ownership.WEB_APP or self.owns(agent_id)

    def origin_of(self, agent_id: str) -> str:
        """"authored" | "installed" | "curated" — how this agent got here. Record-less dirs are
        authored (legacy must keep publishing). Unknown ids are authored too: the caller is about
        to fail on resolve_dir anyway, and provenance should never be the error that hides it."""
        info = self._ownership(agent_id)
        return info[1] if info is not None else ownership.AUTHORED

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
        acct = self._account_id() if callable(self._account_id) else None
        layers = [
            layer
            for layer in (
                (self._overlay(), overlay_path, ownership.presumed_owner(True, acct, self._hosted()))
                if overlay_path is not None
                else None,
                (self._specs, self._agents_dir, self._shared_owner_default()),
            )
            if layer is not None
        ]
        # A DECLARED definition wins over a bare directory, in either layer. The account's agents
        # root holds a folder for every agent the user has chatted with (its sessions/ and
        # workspace/), so "the overlay has a directory by this name" no longer means the account
        # owns a copy — loading one would shadow the real definition with an empty spec. The
        # second pass keeps the one agent allowed to exist undeclared (`main`) loadable.
        for require_declaration in (True, False):
            for specs, root, default_owner in layers:
                d = root / agent_id
                if not d.is_dir():
                    continue
                if require_declaration and not is_definition_dir(d):
                    continue
                # Undeclared is allowed only for `main`, and only from the shared catalogue —
                # the same rule the scan applies (an overlay must not define main).
                if not require_declaration and not (
                    agent_id == "main" and root == self._agents_dir
                ):
                    continue
                spec = self._with_ownership(self._load_dir(agent_id, d), d, default_owner)
                specs[agent_id] = spec
                log.info("agents: added '%s' at runtime from %s", agent_id, root)
                return spec
        raise FileNotFoundError(str((overlay_path or self._agents_dir) / agent_id))

    def create_from(self, agent_id: str, write_files) -> AgentSpec:
        """THE one creation path — the WORLD half of authoring an agent, whoever the author
        is (``create`` below, the agent-builder's create_agent tool, anything later):

          * placement via ``_write_target`` — the caller's overlay when they have one,
          * collision against what this caller can SEE (``_current``: shared + overlay), so
            an overlay skeleton can never shadow a curated id and look like it worked,
          * ownership stamped at birth — the one moment "whose is this" has an unambiguous
            answer; everything downstream (mine, publish, hosted visibility) reads the
            record instead of guessing,
          * live registration — resolvable on the next message, no restart.

        The CONTENT half — which files, what's in them — is ``write_files(dir)``, supplied
        by the caller who owns that grammar. Split by ownership of the knowledge: a second
        creator would have to re-implement placement, collision and stamping to diverge,
        which is exactly the bug (create_agent's DIY path) this method retires."""
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
        write_files(d)
        ownership_store.stamp_create(
            d,
            self._account_id() if callable(self._account_id) else None,
            self._hosted(),
        )

        spec = self._with_ownership(self._load_dir(agent_id, d), d, self._shared_owner_default())
        target_specs[agent_id] = spec
        log.info("agents: created '%s' in %s", agent_id, target_root)
        return spec

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
        pass. Returns the loaded spec. Placement, collision and the ownership stamp are
        ``create_from``'s — this method owns only its CONTENT."""
        agent_id = (agent_id or "").strip().lower()

        def write_files(d: Path) -> None:
            # agent.toml — JSON string literals are valid TOML basic strings, so this
            # safely escapes quotes/backslashes in the name/description.
            lines = [f"name = {json.dumps(name or agent_id)}"]
            if description.strip():
                lines.append(f"description = {json.dumps(description.strip())}")
            mode = (app or "").strip().lower()
            if mode:
                mode = mode if mode in ("browser", "window") else "browser"
                title = name or agent_id
                lines += [
                    "",
                    "[app]",
                    f"title = {json.dumps(title)}",
                    f"mode = {json.dumps(mode)}",
                ]
                ui = d / "ui"
                ui.mkdir(exist_ok=True)
                (ui / "index.html").write_text(
                    _APP_UI_STARTER.replace("__TITLE__", title).replace("__ID__", agent_id),
                    encoding="utf-8",
                )
            (d / "agent.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
            if identity.strip():
                (d / "IDENTITY.md").write_text(identity.strip() + "\n", encoding="utf-8")

        return self.create_from(agent_id, write_files)

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
            specs, root, from_overlay = overlay, overlay_path, True
        else:
            if agent_id not in self._specs:
                raise KeyError(agent_id)
            specs, root, from_overlay = self._specs, self._agents_dir, False

        removed = {"id": agent_id, "definition": False, "sessions": False}
        def_dir = root / agent_id  # definition + workspace/ live here
        if def_dir.is_dir():
            if from_overlay:
                # AN ACCOUNT'S AGENT DIR IS ALSO ITS DATA DIR (one folder per agent: definition
                # + workspace/ + sessions/). Removing the agent must not remove the person's
                # chats and files with it — so the DEFINITION is deleted entry by entry and the
                # user's own subtrees are left standing. Nothing re-lists the agent afterwards:
                # the scan requires an agent.toml, and that is gone.
                for item in sorted(def_dir.iterdir()):
                    if item.name in USER_DATA_DIRS:
                        continue
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                removed["definition"] = not (def_dir / "agent.toml").exists()
                if not any(def_dir.iterdir()):
                    def_dir.rmdir()  # nothing of the user's was in there after all
            else:
                shutil.rmtree(def_dir, ignore_errors=True)
                removed["definition"] = not def_dir.exists()
        state_dir = Path(self._state_dir_for(agent_id))  # <state_dir>/agents/<id>/ (sessions)
        if state_dir.is_dir() and state_dir.resolve() != def_dir.resolve():
            shutil.rmtree(state_dir, ignore_errors=True)
            removed["sessions"] = not state_dir.exists()
        # NOTE (accounts): an account's transcripts live INSIDE its agent dir and are preserved
        # above, so uninstalling leaves them on disk. Deliberate — orphaned data is recoverable
        # and losing a user's history to an uninstall is not — but it means uninstall+reinstall
        # resurrects the old chats. Delete USER_DATA_DIRS here when uninstall grows a "delete my
        # data" flag.
        del specs[agent_id]
        log.info(
            "agents: removed '%s' (definition=%s sessions=%s)",
            agent_id,
            removed["definition"],
            removed["sessions"],
        )
        return removed
