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
            "destination": {
                "type": "string",
                "description": "WHERE it goes. 'org' ships it straight to your organization's "
                "members — internal distribution, no public listing, no review, and dry_run/"
                "confirm/version do not apply. 'marketplace' makes a PUBLIC listing (your first "
                "one files for review). OMIT IT and the right one is chosen: your organization "
                "when you belong to one, the marketplace otherwise.",
            },
            "org_id": {
                "type": "string",
                "description": "which organization to ship to — only needed when you belong to "
                "more than one",
            },
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
    def _not_ready(blocked, action: str = "publish") -> str:
        """The refusal a PERSON reads.

        The old text printed a rule code and a fix line per finding — a validator's output, not an
        answer to "why can't I publish?". Someone who hit it could not tell whether their agent was
        BROKEN or merely UNFINISHED, and the most common cause by far is the second one: the app is
        still the starter template. So the common case gets named in plain words, with the single
        action that resolves it; the codes stay underneath for whoever wants them."""
        placeholders = [f for f in blocked if f.code == "UI_PLACEHOLDER_SHIPPED"]
        if placeholders:
            head = (
                f"Not ready to {action} yet — the app is still partly the starter template.\n\n"
                "Those template widgets show the layout and the wiring, but they are not this "
                "agent's screens. Handing them over would tell whoever installs it that nobody "
                "finished the job.\n\n"
                'WHAT TO DO: ask me to "finish the app" — I will turn the widgets this agent '
                "actually uses into real screens, delete the rest, and try again."
            )
        else:
            head = (
                f"Not ready to {action} yet. These are fine while you are building, but handing "
                "the agent to other people holds a higher bar:"
            )
        detail = "\n".join(f"  [{f.code}] {f.message}\n    -> {f.fix}" for f in blocked)
        return f"{head}\n\nDetails:\n{detail}\n\n(Nothing was built or sent.)"

    def _ship_to_org(self, agent_id: str, agent_dir: Path, params: dict, org_ids) -> ToolResult:
        """Give this agent to the caller's ORGANIZATION — the enterprise path.

        No packing, no signing, no registry, no reviewer: the definition is copied into the org's
        shared layer and every member's registry resolves it read-only from that moment. The
        platform is not a party to this, which is the point — an org distributing to its own people
        is not publishing to the world, and making them queue behind a marketplace review was the
        wrong model.

        WHAT STILL BLOCKS. No reviewer, but not no standard: an org share is a side-loaded install
        to everyone in the company, so it holds the ORG_SHARE bar — which resolves to the same set
        as packing (see domain/rulebook.py). Errors refuse, and so do the warn-level findings that
        exist precisely because they only hurt once somebody else runs the agent: an inlined
        credential, a sandboxed tool that will silently read nothing, and a window still made of
        the template's examples. Removing the queue is the point; removing the floor is not."""
        from agent_runtime.infrastructure import accounts, user_state
        from agent_runtime.infrastructure.agents.org_install import install_org_definition

        org_id = str(params.get("org_id") or "").strip()
        if not org_id:
            if len(org_ids) == 1:
                org_id = org_ids[0]
            elif not org_ids:
                return ToolResult.text(
                    "you do not belong to an organization, so there is nowhere internal to ship "
                    "this. To put it in front of the public instead, publish with "
                    "destination='marketplace'.\n\n(Nothing was sent.)",
                    is_error=True,
                )
            else:
                return ToolResult.text(
                    "you belong to more than one organization — say which with org_id: "
                    + ", ".join(org_ids)
                    + "\n\n(Nothing was sent.)",
                    is_error=True,
                )
        # Membership is re-checked against the VERIFIED token's own claim, never a frame parameter.
        if org_id not in org_ids:
            return ToolResult.text(
                f"you are not a member of '{org_id}'.\n\n(Nothing was sent.)", is_error=True
            )

        if self._validator is not None:
            report = self._validator.validate(agent_id)
            if not report.ok:
                return ToolResult.text(
                    "not shipping to your organization — this agent still has errors, and a "
                    "broken agent would reach every member at once:\n\n"
                    f"{report.as_text()}\n\n(Nothing was sent.)",
                    is_error=True,
                )
            from ..domain.rulebook import ORG_SHARE, blockers

            blocked = [f for f in report.findings if f.code in blockers(ORG_SHARE)]
            if blocked:
                return ToolResult.text(self._not_ready(blocked, "ship to your organization"),
                                       is_error=True)

        # ---- THE CLOUD PATH, PREFERRED -----------------------------------------------------
        # The SAME pipeline as a marketplace publish -- packed, signed, versioned, installer
        # built -- against the ORGANIZATION's own registry instead of the public one. That is
        # what makes "publish once, everyone has it" true: a colleague on another machine
        # installs from that registry exactly as they would a marketplace agent, and a version
        # can be superseded or rolled back like any other.
        #
        # A LOCAL COPY IS THE FALLBACK, NOT THE DEFAULT. It reaches only this machine's own org
        # layer -- right for a checkout, useless for a company -- so when that is what happened
        # the result says so, instead of claiming every member has it.
        from agent_runtime.application.interfaces.bundle_publisher import PublishRequest

        publisher = self._publisher(str(params.get("target") or "").strip())
        if publisher is not None and not publisher.requirements():
            result = publisher.publish(
                PublishRequest(
                    agent_dir=agent_dir,
                    dry_run=False,
                    version=str(params.get("version") or ""),
                    with_installer=True,
                    org_id=org_id,
                )
            )
            if not result.ok:
                return ToolResult.text(
                    f"could not ship to your organization: {result.message}"
                    "\n\n(Nothing was published.)",
                    is_error=True,
                )
            out = [
                (f"Shipped '{agent_id}' {result.version or ''}".rstrip())
                + " to your organization.",
                "",
                "Every member can install it now, on any machine. No review was needed: this is "
                "internal distribution, not a public listing.",
            ]
            if result.installer_url:
                out.append(f"\ninstaller: {result.installer_url}")
            if result.url:
                out.append(f"bundle:    {result.url}")
            return ToolResult.text("\n".join(out))

        # ---- THE LOCAL FALLBACK ------------------------------------------------------------
        author = accounts.account_id() or ""
        target = user_state.org_agents_dir(self._config.state_dir, org_id) / agent_id
        err = install_org_definition(agent_dir, target, org_id, agent_id, author)
        if err:
            return ToolResult.text(
                f"could not ship to the organization: {err}\n\n(Nothing was sent.)", is_error=True
            )
        refresh = getattr(self._registry, "refresh", None)
        if callable(refresh):
            refresh()

        return ToolResult.text(
            f"Shipped '{agent_id}' to your organization ON THIS MACHINE ONLY.\n\n"
            "This build has no publish service wired in (store.publish_url in its "
            "distribution.toml), so the agent was copied into this daemon's own org layer "
            "rather than uploaded. Anyone signed in to THIS daemon resolves it; a colleague on "
            "another computer does not.\n\n"
            "To reach the whole organization, publish from a build with a publish service."
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

        # WHERE THIS GOES — decided before anything is validated, packed or sent, because the two
        # destinations hold genuinely different bars:
        #
        #   org          INTERNAL distribution. Membership is the authorization, nobody reviews it,
        #                no public artifact is made and no version is burned. This is what an
        #                enterprise means by "publish": give it to my people.
        #   marketplace  a PUBLIC listing, with every guard it has always had.
        #
        # Defaulting by membership is the whole fix: an org user pressing Publish used to be routed
        # into the public pipeline — the wrong door — and met a reviewer queue and a validator wall
        # for an agent they only ever wanted their own colleagues to have.
        from agent_runtime.infrastructure import accounts

        org_ids = accounts.org_ids()
        destination = (str(params.get("destination") or "").strip().lower()) or "auto"
        if destination == "auto":
            destination = "org" if org_ids else "marketplace"
        if destination not in ("org", "marketplace"):
            return ToolResult.text(
                f"unknown destination '{destination}' — use 'org' or 'marketplace'.", is_error=True
            )
        if destination == "org":
            return self._ship_to_org(agent_id, agent_dir, params, org_ids)

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
                return ToolResult.text(self._not_ready(blocked), is_error=True)

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
