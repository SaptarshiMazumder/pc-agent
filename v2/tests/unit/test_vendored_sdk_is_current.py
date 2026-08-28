"""Every copy of the client SDK in this checkout must match the build it was copied from.

WHY THIS EXISTS — the outage it is named after. `ui/vendor/agentd-client.js` is COPIED into each
agent, so one SDK lives as a dozen independent files. On 2026-08-15 the accounts service stopped
issuing the pre-token `login.token` field and began returning `access_token`; the SDK was updated
two hours later, and `clients/sdk-js/scripts/vendor.mjs` — which pushes a fresh build into every
copy — was written after the last build had already run. So `dist/` was correct and all twelve
copies were not. Every agent app's sign-in failed with "the accounts server returned no session
token", while the server answered 200 the whole time.

`test_pack_revendors_sdk.py` covers the copy that goes into a PACKAGE. This covers the copies that
sit in the repository and are served straight off disk, which is how agents run in development and
how the desktop build's bundled agents ship.

THE FIX WHEN THIS FAILS is never to edit the copies by hand:

    cd v2/clients/sdk-js && npm run build     # builds dist/ and vendors it everywhere

SKIPPED WHEN `dist/` IS ABSENT, because it is gitignored — a checkout that has never run the Node
build has nothing to compare against, and failing there would report a missing toolchain as a
stale SDK.
"""

from __future__ import annotations

from pathlib import Path

import pytest

V2 = Path(__file__).resolve().parents[2]
DIST = V2 / "clients" / "sdk-js" / "dist"
IIFE = DIST / "agentd-client.js"
ESM = DIST / "index.js"
TYPES = DIST / "index.d.ts"
AGENTS = V2 / "agents"

BUILD_CMD = "cd v2/clients/sdk-js && npm run build"


def _source_for(copy: Path) -> Path:
    """Which build this copy is vendored FROM. Two shapes, and mixing them is a real failure.

    A plain `ui/` loads the SDK with a <script> tag and needs the IIFE. A BUILT app (React/Vite)
    imports `@agentd/client` and needs the ESM bundle plus its types, which is why those targets
    always carry a sibling `.d.ts`. That sibling is the tell, and it is the same distinction
    `scripts/vendor.mjs` draws when it picks a source — asserting one source for both would fail
    on every built app while nothing was actually wrong.
    """
    return ESM if copy.with_suffix(".d.ts").is_file() else IIFE


def _vendored() -> list[Path]:
    """Every vendored SDK THAT IS STILL MAINTAINED.

    REACT APPS AND THE TEMPLATES ONLY — `app/vendor/` and `templates/`. The plain `ui/vendor/`
    copies are deliberately excluded: a dozen older agents carry one, they are served straight off
    disk and keep working on whatever SDK they shipped with, and nothing re-vendors them any more
    (see `scripts/vendor.mjs`). Every agent window is a React project now, so none of those will be
    rebuilt in vanilla — and re-vendoring them meant each SDK build rewrote ten files nobody had
    asked to change, turning an unrelated diff into ten.

    This test and `vendor.mjs` MUST agree about that set. If they drift, either the build rewrites
    files this does not check, or this demands freshness the build no longer provides.
    """
    return sorted(
        p
        for p in AGENTS.rglob("vendor/agentd-client.js")
        if p.parent.parent.name == "app" or "templates" in p.parts
    )


@pytest.mark.skipif(not IIFE.is_file(), reason=f"no SDK build at {DIST} - run `{BUILD_CMD}`")
def test_no_agent_ships_a_stale_sdk():
    copies = _vendored()
    assert copies, "no vendored SDK found at all - the vendor step or the layout has moved"
    stale = [
        f"{p.relative_to(V2).as_posix()} (expected {_source_for(p).name})"
        for p in copies
        if p.read_bytes() != _source_for(p).read_bytes()
    ]
    assert not stale, (
        f"{len(stale)} vendored SDK copy(ies) drifted from dist/: {stale}. "
        f"Do not edit them by hand - run `{BUILD_CMD}`."
    )


@pytest.mark.skipif(not TYPES.is_file(), reason=f"no SDK build at {DIST} - run `{BUILD_CMD}`")
def test_vendored_types_match_the_bundle_beside_them():
    """A built app compiles against these. Types that lag the bundle are worse than none: the
    editor and `tsc` both agree a method exists, and it is missing at runtime."""
    current = TYPES.read_bytes()
    stale = [
        d.relative_to(V2).as_posix()
        for d in sorted(AGENTS.rglob("vendor/agentd-client.d.ts"))
        if d.read_bytes() != current
    ]
    assert not stale, f"stale vendored types: {stale}. Run `{BUILD_CMD}`."


# The bundled apps are the copies pack-time re-vendoring can NEVER reach: Vite compiles the SDK
# INTO the bundle, so there is no `vendor/agentd-client.js` to substitute. Agent Builder is the
# one that ships this way today, and it is the app the outage was reported against.
BUNDLED_APPS = {"agent-builder": AGENTS / "agent-builder" / "ui" / "assets"}


@pytest.mark.parametrize("agent_id", sorted(BUNDLED_APPS))
def test_a_bundled_app_carries_a_post_token_auth_sdk(agent_id: str):
    """The built bundle must ask the RUNTIME for its token (`/auth/token`), not refresh its own.

    A string is a crude thing to assert on, and it is deliberate: the endpoint is the exact fact
    that separates the one-refresher world from the every-window-renews world, it is invisible from the source tree (the source imports `@agentd/client` and looks
    perfectly current), and only a REBUILD of the app can fix it. Nothing else in the repo notices
    that this bundle is older than the SDK sitting beside it.
    """
    assets = BUNDLED_APPS[agent_id]
    bundles = sorted(assets.glob("*.js"))
    assert bundles, f"{agent_id} ships no built bundle at {assets.relative_to(V2).as_posix()}"
    joined = "\n".join(b.read_text(encoding="utf-8", errors="ignore") for b in bundles)
    assert "/auth/token" in joined, (
        f"{agent_id}'s bundle predates runtime-held auth - sign-in will misbehave against a "
        f"current daemon. Rebuild it: cd v2/agents/{agent_id}/app && npm run build"
    )
