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
        "reaches nobody."
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
        root = Path(getattr(self._registry, "agents_dir", "") or "")
        candidate = root / agent_id
        return candidate if (candidate / "agent.toml").is_file() else None

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
                f"no agent '{agent_id}' (looked for agent.toml under "
                f"{getattr(self._registry, 'agents_dir', '?')}).",
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

        with_installer = params.get("with_installer")
        request = PublishRequest(
            agent_dir=agent_dir,
            dry_run=dry_run,
            with_installer=True if with_installer is None else bool(with_installer),
        )
        # Both adapters are blocking (one shells out to aws, the other does a large upload), so the
        # event loop is handed off rather than stalled — a publish takes minutes on a slow link.
        result = await asyncio.to_thread(publisher.publish, request)

        return ToolResult.text(
            self._render(result, publisher.name),
            is_error=not result.ok,
            details={
                "ok": result.ok,
                "agentId": agent_id,
                "bundleId": result.bundle_id,
                "version": result.version,
                "target": publisher.name,
                "dryRun": result.dry_run,
                "pending": result.pending,
                "url": result.url,
                "installerUrl": result.installer_url,
                "warnings": result.warnings,
            },
        )
