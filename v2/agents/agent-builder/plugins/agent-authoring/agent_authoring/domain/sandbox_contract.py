"""What a private tool must look like to survive being INSTALLED — decided by code, not by a model.

An agent's own tools are trusted on the machine that authored them and untrusted on every machine
that installs them (``classify_origin`` judges by provenance: the marketplace ledger). So a tool
can pass every check here, work perfectly for its author, and be broken for every buyer. The three
shapes that do that are known and finite:

    reads os.environ / a key          -> the untrusted grant is ``secrets = {}``, ALWAYS
    imports requests / httpx / socket -> the untrusted grant is ``net_allowlist = ()``
    calls a model without declaring   -> ``needs_model`` is what puts models on the grant; without
      ``needs_model = True``             it the broker refuses with "not granted"

The third is the nastiest, because the other two at least fail somewhere the author can imagine.
A model call is INVERTED under the sandbox — the tool asks, the host performs (model_broker.py) —
and the entire authorisation hinges on one class attribute the author never sees a reason to write.

WHY THIS IS A MODULE AND NOT A PARAGRAPH IN THE SKILL

The builder's own plan ranks the mechanisms: validator rule (absolute) > a rule in the generated
agent's AGENTS.md > prose in a skill read once at build time. Prose asks a model to remember;
this decides. ``derive_model_need`` reads the code it was handed and stamps the attribute, and
``blocking_defects`` refuses the two shapes that can never work, so neither outcome depends on
the model having recalled a rule while it was busy with something else.

ONE definition, TWO consumers — ``create_tool`` (which can fix the code before it exists) and
``SandboxRules`` (which can only report on code that already does). A second copy of these
patterns would be one more thing to drift, which is the failure this whole bundle exists to catch.

PRECISION IS THE POINT, and it is why the patterns are split into two tiers. An `import requests`
is unambiguous, so it is a REFUSAL. A bare ``https://`` in a string is not — it could be a docs
link — so it stays an advisory WARN and never blocks anything. A check that is wrong on working
code gets switched off, and then it protects nobody.
"""

from __future__ import annotations

import re

# --- tier 1: UNAMBIGUOUS. Safe to refuse on, because there is no innocent reading. ------------

#: Reading process env. The grant is ``secrets = {}``, so for a buyer this returns nothing at all
#: — the tool does not error, it silently behaves as if the key were unset.
ENV_READ = re.compile(r"\bos\.environ\b|\bos\.getenv\s*\(|\bgetenv\s*\(|\benvironb\b")

#: Importing a network client. The grant is ``net_allowlist = ()``.
NET_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:httpx|requests|aiohttp|urllib|urllib3|http\.client|socket)\b",
    re.MULTILINE,
)

# --- tier 2: COARSE. Advisory only — these appear in working code often enough to matter. -----

#: A key-shaped name. Frequently a false positive (a parameter called `token`), hence WARN-only.
SECRET_NAME = re.compile(r"[A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*")

#: A URL in the source. Could be a comment, a docs link, or a real call.
URL_LITERAL = re.compile(r"https?://")

# --- model calls: the DERIVATION, not a warning ------------------------------------------------

#: The two host-brokered entry points. These are the ONLY way a sandboxed tool may reach a model,
#: and calling either is what makes ``needs_model = True`` true — so the flag is derived from them
#: rather than asked for.
TEXT_CALL = re.compile(r"\btext_complete\s*\(")
VISION_CALL = re.compile(r"\bvision_complete\s*\(")

#: Set when a tool declares the flag itself (hand-written plugins; create_tool stamps it for you).
DECLARES_NEEDS_MODEL = re.compile(r"^\s*needs_model\s*=\s*True\b", re.MULTILINE)


def derive_model_need(code: str) -> tuple[bool, str]:
    """``(needs_model, model_kind)`` for a tool, read off the code it will run.

    Vision wins when both appear: ``model_kind`` picks which default the resolution chain uses,
    and a tool that looks at an image needs a model that can, whereas the reverse is not true —
    a vision model answers a text prompt fine.

    A tool that calls neither gets ``(False, "text")``, which is the ``Tool`` class default and
    means the sandbox grants it no models. That is correct: a tool with no model call has no use
    for one, and granting it anyway would widen the blast radius of a bug for nothing.
    """
    text = code or ""
    if VISION_CALL.search(text):
        return True, "vision"
    if TEXT_CALL.search(text):
        return True, "text"
    return False, "text"


def declares_model_need(source: str) -> bool:
    """Does this source already set ``needs_model = True``? (For code we did not generate.)"""
    return bool(DECLARES_NEEDS_MODEL.search(source or ""))


def undeclared_model_call(source: str) -> bool:
    """A model call with no declaration — refused at runtime for every installed copy."""
    needs, _ = derive_model_need(source)
    return needs and not declares_model_need(source)


def blocking_defects(code: str) -> list[tuple[str, str, str]]:
    """``(code, what, fix)`` for every tier-1 defect — the shapes that CANNOT work once installed.

    Empty list => nothing unambiguous is wrong. This deliberately does not look at tier 2: a
    refusal has to be right every time, and a caller that blocks on a URL in a comment teaches
    its user to route around it.
    """
    out: list[tuple[str, str, str]] = []
    if ENV_READ.search(code or ""):
        out.append(
            (
                "ENV_READ",
                "reads process environment variables",
                "take the value as a tool PARAMETER instead — an installed tool is granted "
                "secrets = {} and os.environ reads nothing for anyone who downloads this agent",
            )
        )
    if NET_IMPORT.search(code or ""):
        out.append(
            (
                "NET_IMPORT",
                "imports a network client",
                "an installed tool is granted no network. Call a model with "
                "oneshot.text_complete (the host performs it for you), or use a SHARED tool "
                "that already owns the network path",
            )
        )
    return out
