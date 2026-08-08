"""PublishAgentTool — `publish_agent`: build an agent and push it to the marketplace.

ONE PUBLISHER, NOT TWO. This does not reimplement publishing; it calls the very function
`agentd bundle publish` calls (``bundle_cli.run_publish``) with the same argument shape. That
matters because the guards live in there and every one of them is the result of a real accident:

  * publishing rebuilds the WHOLE index, so prior entries are carried forward — otherwise
    releasing one agent silently unpublishes every other agent in the registry
  * a key that does not match the registry's current publisher_key is REFUSED, because
    re-signing with a new key makes every already-installed client reject the whole registry
  * index.json uploads LAST, so a failed artifact upload leaves the registry untouched rather
    than advertising a download that 404s

A second implementation would have to re-earn all of that, and would drift the first time one
side was fixed.

TWO SIGNALS TO PUBLISH FOR REAL. `dry_run` defaults to TRUE and `confirm` defaults to FALSE, so
neither a model deciding to be helpful nor a single mis-click can push a public artifact. The
preview is the useful default anyway: it prints the exact index that would be published, which is
where you notice that a bundle you did not expect is about to change.

NOT CONFIGURED IS NOT AN ERROR IN THE CODE. An install with no `publish_target` cannot publish,
and the tool says so with the two settings to add. That empty default is what stops a downloaded
copy of this product from pushing to someone else's registry.
"""

from __future__ import annotations

import argparse
import contextlib
import io
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class PublishAgentTool(Tool):
    name = "publish_agent"
    label = "Publish Agent"
    default_retryable = False  # it uploads; a retry would re-upload hundreds of megabytes
    description = (
        "Publish an agent to the marketplace registry so other people can install or download "
        "it. Packs the agent (same artifact as package_agent), stages any installer already "
        "built for it, signs the index with this publisher's key, and uploads — artifacts first, "
        "index last. PREVIEWS BY DEFAULT: it prints the exact registry index that would be "
        "published and uploads nothing. To publish for real, pass BOTH dry_run=false and "
        "confirm=true. Requires publish_target and publisher_keyfile in config; without them it "
        "reports what to set and does nothing. VALIDATES FIRST and refuses on errors — a broken "
        "agent must never become a public download. Bump `version` in agent.toml before "
        "publishing a change: installs supersede BY VERSION."
    )
    parameters = {
        "type": "object",
        "required": ["agent_id"],
        "properties": {
            "agent_id": {"type": "string", "description": "the agent to publish (e.g. my-agent)"},
            "dry_run": {
                "type": "boolean",
                "description": "true (default) prints the plan and uploads nothing",
            },
            "confirm": {
                "type": "boolean",
                "description": "must be true, together with dry_run=false, to actually upload",
            },
            "target": {
                "type": "string",
                "description": "override the registry target (s3://bucket[/prefix] or a "
                "directory); normally omit and let config.publish_target decide",
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

    def _missing_settings(self, target: str) -> list[str]:
        missing = []
        if not target:
            missing.append(
                "publish_target — where to publish, e.g. \"s3://my-registry-bucket\" or a local "
                "directory (env: AGENTD_PUBLISH_TARGET)"
            )
        keyfile = str(getattr(self._config, "publisher_keyfile", "") or "")
        if not keyfile:
            missing.append(
                "publisher_keyfile — path to the keypair from `agentd bundle keygen` "
                "(env: AGENTD_PUBLISHER_KEYFILE)"
            )
        elif not Path(keyfile).is_file():
            missing.append(f"publisher_keyfile points at a file that does not exist: {keyfile}")
        return missing

    # ------------------------------------------------------------------ execute
    async def execute(self, tool_call_id, params, abort, on_update=None):
        from agent_runtime.cli.commands import bundle as bundle_cli

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

        # A public artifact must not be broken. Same validator package_agent uses; skipped only
        # when one was not wired in (a build without the validator still publishes rather than
        # refusing for a reason the user cannot act on).
        if self._validator is not None:
            report = self._validator.validate(agent_id)
            if not getattr(report, "ok", True):
                return ToolResult.text(
                    "refusing to publish: validate_agent reports errors. Fix them, then "
                    f"publish.\n\n{getattr(report, 'message', '')}",
                    is_error=True,
                )

        target = str(params.get("target") or getattr(self._config, "publish_target", "") or "").strip()
        missing = self._missing_settings(target)
        if missing:
            lines = "\n".join(f"  * {m}" for m in missing)
            return ToolResult.text(
                "this install is not configured to publish. Set:\n"
                f"{lines}\n\n"
                "Then publish again. (Nothing was built or uploaded.)",
                is_error=True,
            )

        dry_run = params.get("dry_run")
        dry_run = True if dry_run is None else bool(dry_run)
        confirmed = bool(params.get("confirm"))
        if not dry_run and not confirmed:
            return ToolResult.text(
                "refusing to publish without confirmation. This uploads a PUBLIC artifact.\n"
                "Run once with dry_run=true to see the exact index that would be published, "
                "then pass dry_run=false AND confirm=true.",
                is_error=True,
            )

        args = argparse.Namespace(
            agent_dir=[str(agent_dir)],
            to=target,
            key=str(getattr(self._config, "publisher_keyfile", "") or ""),
            name="",
            publisher="",
            version="",
            rotate_key=False,   # a key rotation invalidates every installed client — CLI only
            unsigned=False,     # an unsigned public registry is never what a tool should choose
            dry_run=dry_run,
            publisher_id="",
            roster="",
            no_installers=False,
        )

        # run_publish reports through stdout (it is a CLI entry point). Capture it so the tool
        # returns the same text a publisher would have read in a terminal — including the index
        # listing, which is the part worth checking before confirming.
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = bundle_cli.run_publish(args)
        output = buffer.getvalue().strip() or "(no output)"

        ok = code == 0
        headline = (
            f"PREVIEW — nothing uploaded. Publish for real with dry_run=false and confirm=true."
            if ok and dry_run
            else f"published {agent_id} to {target}"
            if ok
            else f"publish FAILED for {agent_id}"
        )
        return ToolResult.text(
            f"{headline}\n\n{output}",
            is_error=not ok,
            details={
                "ok": ok,
                "agentId": agent_id,
                "target": target,
                "dryRun": dry_run,
                "signed": True,
            },
        )
