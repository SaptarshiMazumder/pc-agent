"""PublishAgentTool — `publish_agent`: put an agent in the marketplace other people install from.

DEPENDS ON A PORT, NOT ON A WAY OF PUBLISHING. `BundlePublisher` has two adapters and the target's
scheme picks one:

  * ``https://…``  the publish SERVICE. The author needs no signing key and no bucket — they sign
    in, and the service signs on their behalf with their own creator key. This is the case that
    matters: an ordinary author cannot be given the key that signs the marketplace, so before this
    seam existed "users can publish" was false however good the tool was.
  * ``s3://…`` or a directory — the OPERATOR path, which is still literally `agentd bundle publish`
    with all of its guards (carry-forward, key-mismatch refusal, index-last).

TWO SIGNALS TO PUBLISH FOR REAL. `dry_run` defaults TRUE and `confirm` defaults FALSE, so neither a
model being helpful nor one mis-click pushes a public artifact. The preview is the useful default
anyway: it states exactly what would be sent, which is where you notice a version you did not mean.

VALIDATES FIRST, with the SAME validator instance `validate_agent` exposes — so "what validate told
you" and "what publish refuses on" cannot diverge. A broken agent must never become a public
download.

PENDING REVIEW IS A NORMAL RESULT. A creator's first publish files for admission to the roster and
comes back 202. Reporting that as an error would send authors looking for a bug in their agent.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class PublishAgentTool(Tool):
    name = "publish_agent"
    label = "Publish Agent"
    default_retryable = False  # it uploads; a retry would re-send the whole package
    description = (
        "Publish an agent to the marketplace so other people can install it — and, when this "
        "machine can build one, ALSO publish the small per-agent installer a stranger with no "
        "agentd downloads and runs. Packs the agent (the same artifact as package_agent), then "
        "either uploads through the publish service (you only need to be signed in) or, on an "
        "operator install, signs and uploads directly. PREVIEWS BY DEFAULT: it states exactly what "
        "would be sent and sends nothing. To publish for real, pass BOTH dry_run=false and "
        "confirm=true. VALIDATES FIRST and refuses on errors. Bump `version` in agent.toml before "
        "publishing a change — installs supersede BY VERSION, so republishing the same number "
        "reaches nobody. DELIVERY: agent.toml's [delivery] table decides how the published agent "
        "reaches people — `web = true` lists an Open-in-browser link (runs on the hosted platform, "
        "requires [app]); `exe = false` skips the standalone installer. Ask the user which they "
        "want before publishing an app agent."
    )
    parameters = {
        "type": "object",
        "required": ["agent_id"],
        "properties": {
            "agent_id": {"type": "string", "description": "the agent to publish (e.g. my-agent)"},
            "dry_run": {
                "type": "boolean",
                "description": "true (default) states the plan and sends nothing",
            },
            "confirm": {
                "type": "boolean",
                "description": "must be true, together with dry_run=false, to actually publish",
            },
            "version": {
                "type": "string",
                "description": "what to ship as. OMIT IT and the patch is bumped automatically "
                "(1.0.0 -> 1.0.1), which is what you want almost every time — installs supersede "
                "BY VERSION, so republishing the same number reaches nobody. Pass 'minor' or "
                "'major' for a bigger step, an exact number like '2.0.0', or 'keep' to leave it "
                "alone when retrying a publish that already bumped.",
            },
            "with_installer": {
                "type": "boolean",
                "description": "also publish the per-agent installer (default true). Without it "
                "only people who already have agentd can install this agent",
            },
            "target": {
                "type": "string",
                "description": "override the publish target (a publish-service https:// url, an "
                "s3:// bucket, or a directory); normally omit and let config.publish_target decide",
            },
        },
    }

    def __init__(self, config, registry, validator=None):
        self._config = config
        self._registry = registry
        self._validator = validator

    # ------------------------------------------------------------------ helpers
    def _agent_dir(self, agent_id: str) -> Path | None:
        # resolve_dir searches the same union discovery reads (overlay + shared catalogue).
        # Building the path from agents_dir — the WRITE target — found only the caller's own
        # layer, so a signed-in user publishing a catalogue agent was told it did not exist.
        resolve = getattr(self._registry, "resolve_dir", None)
        if callable(resolve):
            d = resolve(agent_id)
            return d if d is not None and (d / "agent.toml").is_file() else None
        root = Path(getattr(self._registry, "agents_dir", "") or "")
        candidate = root / agent_id
        return candidate if (candidate / "agent.toml").is_file() else None

    def _owned(self, agent_id: str) -> bool:
        # A registry that cannot answer has no layers, so everything is the caller's.
        owns = getattr(self._registry, "owns", None)
        return bool(owns(agent_id)) if callable(owns) else True

    def _origin(self, agent_id: str) -> str:
        # "authored" | "installed" | "curated"; a registry without provenance means authored.
        origin_of = getattr(self._registry, "origin_of", None)
        return str(origin_of(agent_id)) if callable(origin_of) else "authored"

    def _publisher(self, target: str):
        from agent_runtime.infrastructure.marketplace.publisher_factory import (
            default_stub_provider,
            publisher_for,
        )

        return publisher_for(
            self._config, target, stub_provider=default_stub_provider(self._config)
        )

    @staticmethod
    def _render(result, publisher_name: str) -> str:
        lines = [result.message]
        if result.pending:
            lines.append(
                "\nNothing is listed yet. This is the review step, not a failure — you do not need "
                "to publish again."
            )
        if result.url:
            lines.append(f"\nbundle:    {result.url}")
        if result.installer_url:
            lines.append(f"installer: {result.installer_url}")
        for warning in result.warnings:
            lines.append(f"\nNOTE: {warning}")
        if result.detail:
            lines.append(f"\n--- {publisher_name} ---\n{result.detail}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ execute
    async def execute(self, tool_call_id, params, abort, on_update=None):
        import asyncio

        from agent_runtime.application.interfaces.bundle_publisher import PublishRequest

        agent_id = str(params.get("agent_id") or "").strip()
        if not agent_id:
            return ToolResult.text("publish_agent needs an agent_id.", is_error=True)

        agent_dir = self._agent_dir(agent_id)
        if agent_dir is None:
            return ToolResult.text(
                f"no agent '{agent_id}' with an agent.toml — not among your agents and not in "
                "this deployment's catalogue.",
                is_error=True,
            )

        # Visible is not publishable. Two distinct refusals, because the fix differs: an agent
        # that is NOT YOURS (the deployment's catalogue) versus one that is yours to run but not
        # to ship (a marketplace install — an immutable copy of someone else's work, and
        # publishing it would re-sign that work under this caller's creator identity). Refused
        # here, at the tool, so no amount of UI drift can reopen either hole.
        if not self._owned(agent_id):
            return ToolResult.text(
                f"'{agent_id}' is part of this deployment's catalogue, not one of your agents, "
                "so it is not yours to publish. Agents you create (ask me to build one, or use "
                "the + button) are publishable; installed and curated ones are not.\n\n"
                "(Nothing was built or sent.)",
                is_error=True,
            )
        # Every non-authored origin: marketplace installs, curated seeds, and web-app syncs
        # ("web-app" — the copy a hosted daemon pulls to serve /apps/<id>). All are someone
        # else's published work; re-publishing any of them would re-sign it under this
        # caller's creator identity.
        if self._origin(agent_id) in ("installed", "curated", "web-app"):
            return ToolResult.text(
                f"'{agent_id}' arrived here from the marketplace — it is yours to use, but its "
                "author published it and only they can ship a new version. To build on it, "
                "create your own agent and copy what you need.\n\n"
                "(Nothing was built or sent.)",
                is_error=True,
            )

        # A public artifact must not be broken. Skipped only when no validator was wired in — a
        # build without one still publishes rather than refusing for a reason nobody can act on.
        if self._validator is not None:
            report = self._validator.validate(agent_id)
            if not report.ok:
                # The REPORT, not just the verdict. A refusal that does not say what is wrong is
                # worse than no check: the author is told to fix something and not told what.
                return ToolResult.text(
                    "refusing to publish: validate_agent reports errors. Fix them, then publish."
                    f"\n\n{report.as_text()}",
                    is_error=True,
                )
            # Publishing holds a HIGHER bar than authoring: the rulebook lists codes that are
            # advisory on this machine and unacceptable in a public listing (a version-less
            # publish nobody can ever supersede, exec granted to a web delivery, builder-grade
            # write scope). One table decides — see domain/rulebook.py.
            from ..domain.rulebook import PUBLISH, blockers

            blocked = [f for f in report.findings if f.code in blockers(PUBLISH)]
            if blocked:
                detail = "\n".join(f"  [{f.code}] {f.message}\n    -> {f.fix}" for f in blocked)
                return ToolResult.text(
                    "refusing to publish — these findings are advisory while authoring but "
                    "block a public listing:\n\n" + detail + "\n\n(Nothing was built or sent.)",
                    is_error=True,
                )

        target = str(params.get("target") or "").strip()
        publisher = self._publisher(target)
        if publisher is None:
            # Author first, operator second. Someone who installed the app and pressed Publish is
            # not going to set an environment variable, and telling them to is what made this
            # feature unreachable for the person it exists for. A build that can publish carries
            # its marketplace in its own distribution profile (store.publish_url) — so if we are
            # here, this build simply has no marketplace, and that is not something the author
            # can fix from inside the app.
            return ToolResult.text(
                "this build has no marketplace to publish to, so there is nothing to send.\n\n"
                "If you are running a plain checkout or a BYOK build, that is expected — publishing "
                "needs a build wired to a publish service (store.publish_url in its "
                "distribution.toml).\n"
                "If you are the operator: set publish_target to the service url, or to an s3:// "
                "bucket / local directory to publish directly (env: AGENTD_PUBLISH_TARGET).\n\n"
                "(Nothing was built or sent.)",
                is_error=True,
            )
        missing = publisher.requirements()
        if missing:
            listed = "\n".join(f"  * {m}" for m in missing)
            return ToolResult.text(
                f"cannot publish to {publisher.name} yet:\n{listed}\n\n"
                "(Nothing was built or sent.)",
                is_error=True,
            )

        dry_run = params.get("dry_run")
        dry_run = True if dry_run is None else bool(dry_run)
        confirmed = bool(params.get("confirm"))
        if not dry_run and not confirmed:
            return ToolResult.text(
                "refusing to publish without confirmation. This makes a PUBLIC artifact.\n"
                "Run once with dry_run=true to see exactly what would be sent, then pass "
                "dry_run=false AND confirm=true.",
                is_error=True,
            )

        # THE VERSION, decided here and WRITTEN before anything is packed.
        #
        # The service refuses a number that is not higher than the published one, and its advice
        # was "bump `version` in agent.toml" — a manual step, remembered by nobody, discovered
        # only after a full pack-and-upload had already run. The default is now a patch bump.
        #
        # A DRY RUN CHANGES NOTHING. It reports the number it would ship as; a preview that
        # edited the agent's declaration would be the one thing a preview must never do.
        from ..domain.versioning import VersionError, resolve_version, rewrite_version

        import tomllib

        toml_path = agent_dir / "agent.toml"
        try:
            toml_text = toml_path.read_text(encoding="utf-8")
            current = str(tomllib.loads(toml_text).get("version") or "").strip()
        except (OSError, ValueError) as e:
            return ToolResult.text(f"could not read {toml_path}: {e}", is_error=True)

        try:
            shipping = resolve_version(current, str(params.get("version") or ""))
            updated = rewrite_version(toml_text, shipping) if shipping != current else toml_text
        except VersionError as e:
            return ToolResult.text(f"{e}\n\n(Nothing was built or sent.)", is_error=True)

        if not dry_run and shipping != current:
            try:
                toml_path.write_text(updated, encoding="utf-8")
            except OSError as e:
                return ToolResult.text(f"could not update {toml_path}: {e}", is_error=True)

        with_installer = params.get("with_installer")
        request = PublishRequest(
            agent_dir=agent_dir,
            dry_run=dry_run,
            with_installer=True if with_installer is None else bool(with_installer),
        )
        # Both adapters are blocking (one shells out to aws, the other does a large upload), so the
        # event loop is handed off rather than stalled — a publish takes minutes on a slow link.
        result = await asyncio.to_thread(publisher.publish, request)

        # SAY WHAT HAPPENED TO THE NUMBER. Silently editing an agent's declaration would be the
        # kind of helpfulness nobody can audit; on a dry run this is the whole point, because it
        # is where you notice a version you did not mean.
        note = ""
        if shipping != current:
            note = (
                f"\n\nversion {current or '(none)'} -> {shipping}"
                + (" (would be written on a real publish)" if dry_run else " — agent.toml updated")
            )
        elif str(params.get("version") or "") == "keep":
            note = f"\n\nversion kept at {current} as asked — the service refuses a repeat."

        return ToolResult.text(
            self._render(result, publisher.name) + note,
            is_error=not result.ok,
            details={
                "ok": result.ok,
                "agentId": agent_id,
                "bundleId": result.bundle_id,
                "version": result.version,
                "versionFrom": current,
                "versionShipped": shipping,
                "target": publisher.name,
                "dryRun": result.dry_run,
                "pending": result.pending,
                "url": result.url,
                "installerUrl": result.installer_url,
                "warnings": result.warnings,
            },
        )
