"""The shared modules' styling contract: they consume tokens, the agent supplies them.

WHY THIS IS WORTH A TEST. `_common/` is copied verbatim into every agent that gets a window, and
its stylesheets deliberately define no colours and no fonts — every visual property is a `var()`,
so layout travels with the module and appearance belongs to the agent. That is the right split,
and it has one failure mode: a token the module reads and the starter palette does not define.

Nothing errors when that happens. An unresolved custom property is not invalid CSS — the property
is simply dropped, so the page renders with its structure intact and its colours absent:
transparent cards, inherited text, no accent. It looks like a styling accident rather than a
missing definition, in an agent nobody on this repo will ever open.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "agents/agent-builder/skills/build-agent/templates"
COMMON = ROOT / "_common"
TOKENS = ROOT / "_borrowed/react/src/tokens.css"

#: Set by the shared modules themselves rather than read from the palette — a component may
#: define a token for its own children (none do today; the tuple exists so adding one is a
#: deliberate edit here rather than a silent hole).
DEFINED_BY_MODULES: tuple[str, ...] = ()


def _consumed() -> set[str]:
    """Every `var(--x)` the copied modules read."""
    names: set[str] = set()
    for css in COMMON.rglob("*.css"):
        names |= set(re.findall(r"var\(\s*(--[a-z0-9-]+)", css.read_text(encoding="utf-8")))
    return names


def _defined() -> set[str]:
    """Every `--x: ...` the starter palette declares."""
    return set(re.findall(r"^\s*(--[a-z0-9-]+)\s*:", TOKENS.read_text(encoding="utf-8"), re.M))


def test_the_starter_palette_defines_every_token_the_shared_modules_read():
    missing = _consumed() - _defined() - set(DEFINED_BY_MODULES)
    assert not missing, (
        "these tokens are read by a copied module but defined nowhere in the starter palette, so "
        "every scaffolded agent renders them as nothing: " + ", ".join(sorted(missing))
    )


def test_the_shared_modules_hardcode_no_colours_or_fonts():
    """The other half of the split. A literal colour in a copied module is an agent that cannot be
    restyled — it would keep one page in the builder's palette while the rest of the window moved
    to its own."""
    offenders = []
    for css in COMMON.rglob("*.css"):
        for n, line in enumerate(css.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("/*")[0]
            if re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", code):
                offenders.append(f"{css.name}:{n}: {line.strip()}")
            # a font-family that is not a token reference
            if re.search(r"font-family\s*:", code) and "var(--" not in code:
                offenders.append(f"{css.name}:{n}: {line.strip()}")
    assert not offenders, "hardcoded styling in a copied module:\n  " + "\n  ".join(offenders)


def test_the_scaffold_actually_ships_the_palette():
    """A contract file nobody copies is a document. `STARTER_FILES` is the list that decides."""
    service = (
        Path(__file__).resolve().parents[2]
        / "agents/agent-builder/plugins/agent-authoring/agent_authoring/application"
        / "scaffold_react_app_service.py"
    )
    assert '"src/tokens.css"' in service.read_text(encoding="utf-8")
    assert TOKENS.is_file()
