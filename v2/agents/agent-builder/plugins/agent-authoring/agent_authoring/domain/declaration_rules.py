"""DeclarationRules — is what this agent DECLARES it needs actually coherent?

`[[settings]]`, `[[mcp]]` and `[[oauth]]` are the three blocks that let an agent say "here is
what I need from whoever runs me". They are also the three blocks whose mistakes are INVISIBLE
until somebody else has installed the agent, which is what makes them worth a rule apiece:

  * a settings field nothing reads      -> the user fills it in and nothing happens
  * an MCP server with no credential    -> the agent silently has no tools
  * `[tools] allow = ["aws__*"]` and no `[[mcp]] aws` -> the same, with the author certain
                                           they wired it up
  * a credential inlined into agent.toml -> the author's own key ships to every buyer

Every one of those looks fine on the machine it was built on. That is the whole category.

Pure rules, no I/O: the caller hands over the parsed agent.toml and this only judges. Written
LAST, deliberately, against finished behaviour — a validator written alongside a moving rule
validates a version of the rule that no longer exists.
"""

from __future__ import annotations

import re

from .finding import ERROR, INFO, WARN, Finding

#: Daemon-owned names. `AGENTD_*` is unambiguous: it is the daemon's own configuration.
#:
#: The PROVIDER keys are injected rather than pattern-matched, and the difference matters. The
#: first version of this rule flagged anything ending in `_API_KEY` — which is precisely the
#: naming convention the build-agent skill tells authors to use (`COINBASE_API_KEY`,
#: `ACME_API_KEY`). A check that fires on its own documented example is one people switch off.
#: So: the exact list of keys the daemon actually shares, and nothing else.
RESERVED_PREFIXES = ("AGENTD_",)

#: What a credential ACCIDENTALLY COMMITTED tends to look like. Deliberately conservative: this
#: fires an ERROR, so a false positive would block a legitimate ship. Real prefixes only.
_SECRET_SHAPES = re.compile(
    r"""(sk-[A-Za-z0-9_-]{16,}      # OpenAI / Anthropic style
        |AKIA[0-9A-Z]{12,}          # AWS access key id
        |ghp_[A-Za-z0-9]{20,}       # GitHub PAT
        |xox[baprs]-[A-Za-z0-9-]{10,}   # Slack
        |AIza[0-9A-Za-z_-]{20,})    # Google API key
    """,
    re.VERBOSE,
)

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_:-]+)\}")


class DeclarationRules:
    """Do this agent's `[[settings]]`, `[[mcp]]` and `[[oauth]]` blocks hang together?

    :param provider_keys: the machine-wide credential names this daemon shares
        (``PROVIDER_ENV_KEYS``). Injected from the composition root, like ``UiRules``' method
        vocabulary — the rule must not guess at a list the runtime owns. Empty simply skips
        that one check rather than failing to construct.
    """

    name = "declarations"

    def __init__(self, provider_keys=()):
        self._provider_keys = frozenset(provider_keys or ())

    def check(self, spec, raw_toml: dict, files: list[str], sources: dict | None = None) -> list[Finding]:
        raw = raw_toml or {}
        settings = [s for s in (raw.get("settings") or ()) if isinstance(s, dict)]
        servers = [m for m in (raw.get("mcp") or ()) if isinstance(m, dict)]
        logins = [o for o in (raw.get("oauth") or ()) if isinstance(o, dict)]
        if not (settings or servers or logins):
            return []

        declared = {str(s.get("key") or "").strip() for s in settings} - {""}
        out: list[Finding] = []
        out += self._inlined_credentials(raw)
        out += self._reserved_names(settings)
        out += self._unused_settings(settings, servers, logins, sources or {})
        out += self._mcp_servers(servers, declared, logins)
        out += self._oauth(logins, declared)
        out += self._shipping(raw, settings, servers, logins)
        out += self._authored_setting_values(settings, sources or {})
        out += self._secret_defaults(settings)
        return out

    # ---- the one that decides WHOSE account ---------------------------------
    def _secret_defaults(self, settings: list[dict]) -> list[Finding]:
        """A `default` on a `kind = "secret"` field.

        `default` is the one part of a setting whose VALUE travels — it is how an author says
        "start on this model" or "this endpoint unless you change it". A secret with a default
        is therefore a credential shipped to every installer, which is a contradiction in terms
        rather than a risky choice. The runtime drops it too, loudly; this is so the author
        hears about it while they can still fix it, instead of from a buyer.
        """
        out: list[Finding] = []
        for row in settings:
            key = str(row.get("key") or "").strip()
            if not key or str(row.get("kind") or "").strip().lower() != "secret":
                continue
            if not str(row.get("default") or "").strip():
                continue
            out.append(
                Finding(
                    level=ERROR,
                    code="SETTING_SECRET_DEFAULT",
                    message=f"[[settings]] {key} is a secret and declares a `default`. A value "
                    f"that travels to everyone who installs this agent is not a secret",
                    path="agent.toml",
                    fix=f"remove `default` from {key} — a secret is filled in by whoever runs "
                    f"the agent. `default` is for non-secret fields (a model, an endpoint, a "
                    f"mode) where the author's choice is a starting point, not a credential",
                )
            )
        return out

    def _authored_setting_values(self, settings: list[dict], sources: dict) -> list[Finding]:
        """A VALUE written into agent.config.json for a field the OWNER is supposed to fill in.

        Declaring `[[settings]]` is the author's job; supplying the value is the owner's. The two
        get confused because a plausible default is usually sitting right there — a profile name
        the machine already has configured, a localhost URL — and filling it in makes the agent
        work on the first run without anyone being asked.

        That convenience is the whole problem. A referenced `${NAME}` with no value is what stops
        a declared server from starting, and that refusal is how the owner gets ASKED which account
        the agent acts on. Write the value and the server comes up on credentials nobody chose,
        which looks exactly like success. Observed: an authoring agent filled in the credential
        setting of an agent it was building, from a value already present on the machine, and
        connected seven servers to the owner's live cloud account without anyone being asked.

        It also SHIPS. agent.config.json travels inside the package, so the author's answer becomes
        every installer's default.
        """
        raw = sources.get("agent.config.json")
        if not raw:
            return []
        try:
            import json

            values = (json.loads(raw) or {}).get("settings")
        except (ValueError, AttributeError):
            return []  # unparseable is a different problem, and not this rule's to report
        if not isinstance(values, dict):
            return []
        declared = {str(f.get("key") or "").strip() for f in settings} - {""}
        filled = sorted(k for k, v in values.items() if k in declared and str(v).strip())
        if not filled:
            return []
        return [
            Finding(
                level=ERROR,
                code="AUTHORED_SETTING_VALUE",
                message=(
                    f"agent.config.json supplies values for declared settings "
                    f"({', '.join(filled)}). Those belong to whoever RUNS the agent, not to "
                    f"whoever wrote it — including when there is an obvious default and when a "
                    f"value is already configured on this machine. Declare the field, leave it "
                    f"empty, and ask the owner to fill it in on the agent's settings page"
                ),
                path="agent.config.json",
                fix=(
                    "remove those keys from the settings block; if you need them filled to "
                    "verify the agent, ask the owner to set them and wait"
                ),
            )
        ]

    # ---- the one that costs money ------------------------------------------
    def _inlined_credentials(self, raw: dict) -> list[Finding]:
        """A real key written into agent.toml. ERROR, because agent.toml SHIPS.

        Checked across the whole file rather than only the declaration blocks: the mistake is
        pasting a working value wherever it seemed to belong, and `[[mcp]] env` is only the most
        likely of several places.
        """
        hits = sorted({m.group(1)[:8] for m in _SECRET_SHAPES.finditer(_flatten(raw))})
        if not hits:
            return []
        return [
            Finding(
                level=ERROR,
                code="CREDENTIAL_IN_AGENT_TOML",
                message=(
                    f"agent.toml appears to contain a real credential ({', '.join(hits)}…). "
                    f"This file is PACKAGED — publishing it sends your key to everyone who "
                    f"installs the agent"
                ),
                path="agent.toml",
                fix=(
                    "declare it as [[settings]] and reference it as ${NAME}; the value then "
                    "lives in each user's own .env. Rotate the key that was written here"
                ),
            )
        ]

    def _reserved_names(self, settings: list[dict]) -> list[Finding]:
        out: list[Finding] = []
        for field in settings:
            key = str(field.get("key") or "").strip()
            reserved = key.startswith(RESERVED_PREFIXES) or key in self._provider_keys
            if key and reserved:
                out.append(
                    Finding(
                        level=WARN,
                        code="SETTING_SHADOWS_SHARED_KEY",
                        message=(
                            f"[[settings]] declares `{key}`, which is a machine-wide name "
                            f"(provider keys and AGENTD_* belong to the daemon, not to one agent)"
                        ),
                        path="agent.toml",
                        fix=(
                            "name it after YOUR service — the user's provider keys are already "
                            "on the settings page and shared by every agent"
                        ),
                    )
                )
        return out

    def _unused_settings(
        self, settings: list[dict], servers: list[dict], logins: list[dict], sources: dict
    ) -> list[Finding]:
        """A field the user is asked to fill in that nothing ever reads.

        Either a typo (`${COINBASE_KEY}` against a declared `COINBASE_API_KEY`) or a leftover.
        Both look identical to whoever installs it: they type in a credential, and nothing works.

        WARN, not ERROR — a plugin may read it straight from the environment in code this rule
        cannot see, which is legitimate.
        """
        referenced = set()
        for block in (*servers, *logins):
            referenced |= {m.group(1) for m in _PLACEHOLDER.finditer(_flatten(block))}
        for src in sources.values():
            referenced |= {m.group(1) for m in _PLACEHOLDER.finditer(src)}

        out: list[Finding] = []
        for field in settings:
            key = str(field.get("key") or "").strip()
            if key and key not in referenced:
                out.append(
                    Finding(
                        level=WARN,
                        code="SETTING_NEVER_USED",
                        message=(
                            f"[[settings]] declares `{key}`, but no [[mcp]], [[oauth]] or app "
                            f"file references ${{{key}}} — the user would fill it in and nothing "
                            f"would happen"
                        ),
                        path="agent.toml",
                        fix=(
                            f"reference it as ${{{key}}} where it is needed, remove it, or "
                            f"ignore this if a plugin reads it from the environment directly"
                        ),
                    )
                )
        return out

    # ---- MCP ---------------------------------------------------------------
    def _mcp_servers(self, servers: list[dict], declared: set, logins: list[dict]) -> list[Finding]:
        out: list[Finding] = []
        login_names = {str(o.get("name") or "").strip() for o in logins}
        for server in servers:
            name = str(server.get("name") or "").strip()
            label = name or "(unnamed)"
            command = server.get("command") or ()
            url = str(server.get("url") or "").strip()

            if not name:
                out.append(
                    Finding(
                        level=ERROR,
                        code="MCP_NO_NAME",
                        message="an [[mcp]] block has no `name` — it is dropped at load",
                        path="agent.toml",
                        fix="give it a short name; its tools appear as <name>__<tool>",
                    )
                )
            if bool(command) == bool(url):
                out.append(
                    Finding(
                        level=ERROR,
                        code="MCP_TRANSPORT",
                        message=(
                            f"[[mcp]] {label} sets "
                            f"{'both `command` and `url`' if command else 'neither `command` nor `url`'}"
                            f" — it is dropped at load, and the agent silently has none of its tools"
                        ),
                        path="agent.toml",
                        fix="exactly one: `command` for a local process, `url` for an endpoint",
                    )
                )

            # Credentials it references but nobody declares.
            for ref in sorted({m.group(1) for m in _PLACEHOLDER.finditer(_flatten(server))}):
                if ref.startswith("oauth:"):
                    continue
                if ref not in declared:
                    out.append(
                        Finding(
                            level=ERROR,
                            code="MCP_UNDECLARED_SETTING",
                            message=(
                                f"[[mcp]] {label} references ${{{ref}}}, which no [[settings]] "
                                f"block declares — the daemon refuses to connect a server whose "
                                f"credential is empty, so this agent would have no tools"
                            ),
                            path="agent.toml",
                            fix=f'add [[settings]] with key = "{ref}"',
                        )
                    )

            auth = str(server.get("auth") or "").strip()
            if auth.startswith("oauth:") and auth.split(":", 1)[1] not in login_names:
                out.append(
                    Finding(
                        level=ERROR,
                        code="MCP_UNDECLARED_OAUTH",
                        message=(
                            f"[[mcp]] {label} uses auth = \"{auth}\", but no [[oauth]] block is "
                            f"named '{auth.split(':', 1)[1]}'"
                        ),
                        path="agent.toml",
                        fix="declare the [[oauth]] connection, or use static headers instead",
                    )
                )
        return out

    # ---- the silent-no-tools one -------------------------------------------
    def _allow_without_server(self, raw: dict, servers: list[dict]) -> list[Finding]:
        """`[tools] allow = ["aws__*"]` matching no declared server.

        THE failure this whole feature exists to prevent, seen from the other side: the author
        allowed a namespace, the namespace never arrives, and every symptom is the model saying
        it cannot do the thing.
        """
        tools = raw.get("tools")
        allow = (tools or {}).get("allow") if isinstance(tools, dict) else None
        if not isinstance(allow, list):
            return []
        names = {str(s.get("name") or "").strip() for s in servers}
        out: list[Finding] = []
        for entry in allow:
            text = str(entry)
            if "__" not in text:
                continue
            namespace = text.split("__", 1)[0]
            if namespace and namespace not in names:
                out.append(
                    Finding(
                        level=WARN,
                        code="ALLOW_UNKNOWN_MCP_NAMESPACE",
                        message=(
                            f"[tools] allow lists `{text}`, but no [[mcp]] block declares a "
                            f"server called '{namespace}' — on another machine those tools do "
                            f"not exist"
                        ),
                        path="agent.toml",
                        fix=(
                            f"declare [[mcp]] name = \"{namespace}\" so the connection travels "
                            f"with the agent, or drop the entry. A server added with add_mcp "
                            f"lives in THIS machine's config and is not packaged"
                        ),
                    )
                )
        return out

    # ---- OAuth -------------------------------------------------------------
    def _oauth(self, logins: list[dict], declared: set) -> list[Finding]:
        out: list[Finding] = []
        for login in logins:
            name = str(login.get("name") or "").strip() or "(unnamed)"
            has_urls = bool(str(login.get("authorize_url") or "").strip()) and bool(
                str(login.get("token_url") or "").strip()
            )
            if not str(login.get("server") or "").strip() and not has_urls:
                out.append(
                    Finding(
                        level=ERROR,
                        code="OAUTH_NO_ENDPOINTS",
                        message=(
                            f"[[oauth]] {name} has neither a `server` to discover endpoints from "
                            f"nor both `authorize_url` and `token_url` — Connect would go nowhere"
                        ),
                        path="agent.toml",
                        fix="add `server`, or both explicit URLs",
                    )
                )
            if has_urls and not str(login.get("client_id") or "").strip():
                out.append(
                    Finding(
                        level=WARN,
                        code="OAUTH_NO_CLIENT_ID",
                        message=(
                            f"[[oauth]] {name} names explicit endpoints but no `client_id` — a "
                            f"provider without dynamic registration will refuse the sign-in"
                        ),
                        path="agent.toml",
                        fix='set client_id = "${YOUR_CLIENT_ID}" and declare it in [[settings]]',
                    )
                )
            for ref in sorted({m.group(1) for m in _PLACEHOLDER.finditer(_flatten(login))}):
                if ref not in declared:
                    out.append(
                        Finding(
                            level=ERROR,
                            code="OAUTH_UNDECLARED_SETTING",
                            message=(
                                f"[[oauth]] {name} references ${{{ref}}}, which no [[settings]] "
                                f"block declares"
                            ),
                            path="agent.toml",
                            fix=f'add [[settings]] with key = "{ref}"',
                        )
                    )
        return out

    # ---- shipping ----------------------------------------------------------
    def _shipping(
        self, raw: dict, settings: list[dict], servers: list[dict], logins: list[dict]
    ) -> list[Finding]:
        out = self._allow_without_server(raw, servers)
        out.append(
            Finding(
                level=INFO,
                code="DECLARATIONS_ARE_LOCAL_ONLY",
                message=(
                    "this agent declares settings, MCP servers or sign-ins, so it is a DESKTOP "
                    "agent: the values live in one machine's .env, a stdio server spawns a local "
                    "process, and the OAuth callback is a loopback URL. A hosted daemon serves "
                    "every account from one container, where all three are wrong"
                ),
                path="agent.toml",
                fix="say so when you publish it; set requires_local = true to make it explicit",
            )
        )
        caps = raw.get("capabilities")
        workshop = (caps or {}).get("mcp_workshop") if isinstance(caps, dict) else None
        if servers and workshop is None:
            out.append(
                Finding(
                    level=INFO,
                    code="MCP_WORKSHOP_INHERITED",
                    message=(
                        "this agent declares its servers but says nothing about `mcp_workshop`, "
                        "so it INHERITS the daemon's setting — on a machine where that is on, the "
                        "model could connect arbitrary servers from chat text"
                    ),
                    path="agent.toml",
                    fix="[capabilities] mcp_workshop = false, unless it genuinely needs to",
                )
            )
        return out


def _flatten(value) -> str:
    """Every string in a nested toml value, joined — so a scan cannot miss a nested table."""
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(v) for v in value)
    return str(value)
