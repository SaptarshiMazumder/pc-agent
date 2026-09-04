"""SandboxRules — will this agent's OWN tools still work once they are treated as untrusted?

Pure rules, no I/O — the caller passes each private plugin's source text in.

WHY THIS EXISTS — AND WHY IT IS ABOUT *SHIPPING*, NOT ABOUT HERE
``classify_origin()`` decides trust by PROVENANCE: an agent's private tools are untrusted when
the marketplace ledger says that agent arrived in a ``.agentpkg``. So on THIS machine, an agent
you authored (or one that shipped with the product) keeps its tools trusted — owning tools is
not itself suspicious.

The catch is what happens NEXT. The moment someone installs your agent, it lands in THEIR ledger,
and every tool in ``agents/<id>/plugins/`` becomes THIRD_PARTY_BUNDLE over there. That is exactly
the folder ``create_tool(agent=...)`` writes to. ``DefaultCapabilityResolver`` then hands it:
    fs      -> the run's workspace only
    net     -> nothing
    secrets -> {}   ALWAYS. It never sees a provider key.

So these checks answer one question: *will this agent still work once someone else installs it?*
A tool that reads an API key runs fine for its author and silently reads nothing for everybody
who downloads it — the worst possible failure shape, and invisible without this warning.

ADVISORY, NOT A GATE — deliberately.
``sandbox_untrusted_plugins`` defaults OFF, and even when ON the shipped backend
(``LocalPluginSandbox``) is an in-process passthrough that does not enforce the grant. Nothing
here is broken today, so every finding is INFO/WARN — never ERROR. They exist so an author learns
at WRITING time that such a tool is living on borrowed time: it breaks the day a real isolating
backend lands, and it breaks on the machine of anyone who installs the agent.
"""

from __future__ import annotations

import re
import tomllib

from .finding import INFO, WARN, Finding
from .py_comment_stripper import PyCommentStripper
from .sandbox_contract import (
    ENV_READ,
    NET_IMPORT,
    SECRET_NAME,
    SPAWN,
    URL_LITERAL,
    undeclared_model_call,
)

# Signals that a private plugin wants something the untrusted grant denies. Deliberately a
# coarse source scan: the point is to prompt a human decision, not to prove anything.
#
# The patterns themselves live in sandbox_contract, because create_tool needs the same answers
# and can act on them EARLIER — it refuses to write the unambiguous ones at all. Two copies of
# "what breaks under the sandbox" would eventually disagree, and then the tool and the validator
# would be telling an author different things about the same code.
SECRET_PATTERNS = (ENV_READ, SECRET_NAME)
NETWORK_PATTERNS = (NET_IMPORT, URL_LITERAL)

_STRIPPER = PyCommentStripper()


class SandboxRules:
    """Forward-looking checks over the agent's OWN plugins/ tier."""

    name = "sandbox"

    @staticmethod
    def _declared(plugin_toml: str) -> dict:
        """The plugin's own `[sandbox]` grants. {} when absent or unreadable — an unparseable
        manifest means the plugin does not load at all, which other rules report; here it just
        means "declared nothing", which is the strictest reading and the right default."""
        try:
            return dict(tomllib.loads(plugin_toml or "").get("sandbox") or {})
        except (ValueError, TypeError):
            return {}

    def check(self, spec, raw_toml: dict, files: list[str], sources: dict) -> list[Finding]:
        """`sources` maps a plugins/<pid>/<module>.py path -> its text (already read by infra)."""
        plugin_ids = sorted(
            {
                rel.split("/")[1]
                for rel in files
                if rel.startswith("plugins/") and len(rel.split("/")) >= 3
            }
        )
        if not plugin_ids:
            return []

        out: list[Finding] = [
            Finding(
                level=INFO,
                code="PRIVATE_TOOLS_UNTRUSTED",
                message=(
                    f"this agent ships {len(plugin_ids)} private plugin(s) "
                    f"({', '.join(plugin_ids)}). They are trusted HERE (this agent was not "
                    f"installed from a package), but once someone else installs this agent its "
                    f"ledger marks it THIRD_PARTY_BUNDLE and these tools get sandboxed"
                ),
                path="plugins/",
            )
        ]
        for pid in plugin_ids:
            # COMMENTS AND DOCSTRINGS OUT FIRST. A rule that fires on prose describing the
            # correct behaviour is the report an author switches off — this file's own docstring
            # ("the ${SECRET} path has its own tests") tripped the secrets rule the day it was
            # written. String VALUES are kept: os.environ["ACME_API_KEY"] is the true positive.
            text = "\n".join(
                _STRIPPER.strip(src)
                for rel, src in sources.items()
                if rel.startswith(f"plugins/{pid}/") and rel.endswith(".py")
            )
            if not text:
                continue
            declared = self._declared(sources.get(f"plugins/{pid}/plugin.toml", ""))
            # A `${NAME}` placeholder for a name the plugin DECLARED in [sandbox] secrets is not
            # a secret read — it is the mechanism this rule's own fix line prescribes, and the
            # host substitutes it whether the plugin is sandboxed or not. Scanning it would flag
            # a plugin for doing the correct thing the moment its secret was called *_TOKEN.
            scanned = text
            for name in declared.get("secrets") or ():
                scanned = scanned.replace("${" + str(name) + "}", "")
            if any(p.search(scanned) for p in SECRET_PATTERNS):
                out.append(
                    Finding(
                        level=WARN,
                        code="UNTRUSTED_WANTS_SECRETS",
                        message=f"plugins/{pid}/ reads environment variables / API keys. That "
                        f"works for you, but an installed agent is granted secrets = {{}} — for "
                        f"everyone who downloads this, it will read nothing",
                        path=f"plugins/{pid}/",
                        fix=f"take the value as a tool PARAMETER, or have whoever installs it "
                        f'vouch for the agent via config sandbox_trusted_agents = ["{spec.id}"]',
                    )
                )
            # A raw client is ALWAYS wrong — a sandboxed child has no socket, whatever it dials.
            # That is unchanged by the open network (2026-09): reach is unrestricted, but it is
            # the HOST's reach, through the brokered `fetch`. A plugin using `fetch` with any
            # URL and no [sandbox] net at all is now the CORRECT shape — net only matters when a
            # ${SETTING} appears in a URL — so only the own-client import is flagged.
            if NET_IMPORT.search(text):
                out.append(
                    Finding(
                        level=WARN,
                        code="UNTRUSTED_WANTS_NETWORK",
                        message=f"plugins/{pid}/ makes network calls with its own client. An "
                        f"installed agent never gets a socket — outbound goes through the host",
                        path=f"plugins/{pid}/",
                        fix="use `from agent_runtime.infrastructure.net.outbound import fetch` "
                        "— it reaches any host through the daemon. A credential rides as "
                        "${NAME} listed under [sandbox] secrets, and the plugin never sees its "
                        "value",
                    )
                )
            # THE HOST THE USER BRINGS. A plugin that builds its URL from a `${SETTING}` is
            # calling wherever the person running it pointed that setting — which is legitimate
            # and the whole reason `[sandbox] net = ["${SETTING}"]` exists. What is NOT
            # legitimate is doing that while declaring only fixed hosts: it works perfectly for
            # the author (unsandboxed, any host) and is refused for every single installer,
            # AFTER validate, pack and publish have all passed. That silent-until-sold failure
            # is exactly what this rule exists to convert into a finding.
            used = {
                m.group(1)
                for m in re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text)
            }
            declared_hosts = tuple(declared.get("net") or ())
            placeholder_hosts = {
                m.group(1)
                for h in declared_hosts
                for m in [re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", str(h).strip())]
                if m
            }
            setting_keys = {
                str(row.get("key") or "").strip()
                for row in (raw_toml.get("settings") or [])
                if isinstance(row, dict)
            }
            # Only a setting that plausibly IS an endpoint — a key the agent declares, used in
            # this plugin's source, and not already covered by a ${…} entry in net.
            url_ish = {
                k
                for k in (used & setting_keys) - placeholder_hosts
                if any(w in k.upper() for w in ("URL", "HOST", "ENDPOINT", "BASE", "SERVER"))
            }
            if url_ish and "fetch(" in text:
                names = ", ".join(sorted(url_ish))
                out.append(
                    Finding(
                        level=WARN,
                        code="UNTRUSTED_HOST_FROM_SETTING",
                        message=f"plugins/{pid}/ builds a request from ${{{names}}} — a host the "
                        f"user supplies — but [sandbox] net does not name that setting, so the "
                        f"placeholder is refused as an undeclared name when a buyer's install "
                        f"runs it sandboxed. Works for you, fails for them",
                        path=f"plugins/{pid}/plugin.toml",
                        fix=f"name the setting:  [sandbox]  "
                        f'net = ["${{{sorted(url_ish)[0]}}}"]  — that is what makes the '
                        f"placeholder legal in a URL; it resolves per caller at request time",
                    )
                )
            # Spawning is a hard denial (child_guard._SPAWN), not a degradation. Found in the
            # wild: a shipped agent revealed files with subprocess.Popen(["explorer.exe", ...]) —
            # perfect for its author, dead for every buyer, and nothing said a word.
            if SPAWN.search(text):
                out.append(
                    Finding(
                        level=WARN,
                        code="UNTRUSTED_WANTS_SPAWN",
                        message=f"plugins/{pid}/ starts another process. The sandbox denies "
                        f"subprocess.Popen / os.system / os.exec* outright, so this raises "
                        f"SandboxDenied for everyone who installs this agent",
                        path=f"plugins/{pid}/",
                        fix="do the work in Python, or use a SHARED tool that already owns that "
                        f'capability — or have whoever installs it set sandbox_trusted_agents = '
                        f'["{spec.id}"]',
                    )
                )
            # A model call with no `needs_model = True` is not a judgement call like the two
            # above — it is a certain refusal. `needs_model` is the ONLY thing that puts models
            # on the sandbox grant, so the broker answers "not granted" to every call this tool
            # makes, on every machine that installs the agent. `create_tool` derives the flag,
            # so this only ever fires on a plugin authored with `write`.
            if undeclared_model_call(text):
                out.append(
                    Finding(
                        level=WARN,
                        code="UNTRUSTED_MODEL_UNDECLARED",
                        message=f"plugins/{pid}/ calls a model but does not declare "
                        f"needs_model = True. Sandboxed, the host refuses every one of those "
                        f"calls with 'not granted' — silently working for you and failing for "
                        f"everyone who installs this agent",
                        path=f"plugins/{pid}/",
                        fix="add `needs_model = True` (and `model_kind = \"vision\"` if it reads "
                        "images) as class attributes on the Tool, next to `parameters`",
                    )
                )
        return out
