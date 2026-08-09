"""PackageAgentService — the use case: "turn this agent into something I can give someone".

Owns the ORDER and the one policy decision; performs no I/O and builds no manifest itself.

    validate  ->  derive the manifest  ->  pack  ->  report

VALIDATE FIRST, AND REFUSE ON ERROR. This is the only place in the bundle where a validation
finding blocks anything. The reason is that a bundle is the moment work stops being local: a
broken agent on your own disk is a thing you fix in the next message, while a broken agent
inside a ``.agentpkg`` is a thing someone else downloads.

BY THE SAME ARGUMENT, FOUR WARNINGS BLOCK HERE TOO. The sandbox findings are advisory during
authoring — correct, because on the author's own machine nothing is broken. Packaging is the
moment that stops being true: a ``.agentpkg`` exists to be installed, and on the installing
machine those tools ARE untrusted. A tool that spawns a process or reads ``os.environ`` is not
"forward-looking" at that point; it is a tool that will not run for the person downloading it.

Only the UNAMBIGUOUS ones (``_SHIPS_BROKEN``). A URL in a docstring stays advisory — a gate that
fires on a comment is a gate authors learn to route around, and then it guards nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Findings that are advisory on the author's machine and CERTAIN breakage on a buyer's. Each is
#: tier-1 in ``sandbox_contract`` — an unambiguous shape, never a heuristic — because this list
#: blocks a release and a false positive here is worse than the defect it would catch.
_SHIPS_BROKEN = frozenset(
    {
        "UNTRUSTED_WANTS_SECRETS",     # granted secrets = {} — reads nothing, silently
        "UNTRUSTED_WANTS_NETWORK",     # imports a client; a sandboxed tool has no socket
        "UNTRUSTED_WANTS_SPAWN",       # subprocess/os.system — denied outright
        "UNTRUSTED_MODEL_UNDECLARED",  # no needs_model => the broker refuses every call
    }
)


@dataclass(frozen=True)
class PackageResult:
    """What the tool layer renders. ``report`` is present whenever validation ran."""

    ok: bool
    message: str
    path: str = ""
    sha256: str = ""
    size: int = 0
    bundle_id: str = ""
    version: str = ""
    private_plugins: tuple[str, ...] = ()


class PackageAgentService:
    def __init__(self, reader, packer, defaults, validator):
        self._reader = reader
        self._packer = packer
        self._defaults = defaults
        self._validator = validator

    def package(self, agent_id: str, out_dir: str = "", version: str = "") -> PackageResult:
        agent_id = (agent_id or "").strip()
        if not agent_id:
            return PackageResult(False, "package_agent needs an `agent_id`")

        agent_dir = self._reader.agent_dir(agent_id)
        if agent_dir is None or not agent_dir.is_dir():
            known = ", ".join(sorted(self._reader.known_ids())) or "none"
            return PackageResult(
                False, f"no agent '{agent_id}' with a directory on disk (known: {known})"
            )

        # Gate 2: findings that are ADVISORY locally and certain breakage once installed. See
        # the module docstring — packaging is the moment "it works on my machine" stops being
        # the relevant question. Checked before the error gate so the message is the specific
        # one; a spawn in a private plugin is not a generic "validation failed".
        report = self._validator.validate(agent_id)
        dead_on_arrival = [f for f in report.findings if f.code in _SHIPS_BROKEN]
        if dead_on_arrival:
            detail = "\n".join(
                f"  [{f.code}] {f.message}\n    -> {f.fix}" for f in dead_on_arrival
            )
            return PackageResult(
                False,
                "refusing to package — this agent ships private tools that CANNOT RUN on the "
                "machine of anyone who installs it. They work for you because you authored them "
                "here; installing records the agent in the buyer's ledger, and from then on its "
                "own tools are untrusted.\n\n" + detail + "\n\nFix these, or move that work to a "
                "SHARED tool (which is never sandboxed), then call package_agent again.",
            )

        # Gate: never ship something already known to be broken.
        if not report.ok:
            return PackageResult(
                False,
                "refusing to package — validation failed. Fix these, then call package_agent "
                "again:\n\n" + report.as_text(),
            )

        manifest = self._defaults.manifest(
            agent_id=agent_id,
            agent_toml=self._reader.raw(agent_dir),
            bundle_toml=self._packer.bundle_table(agent_dir),
            private_plugin_ids=self._packer.private_plugin_ids(agent_dir),
            version=version,
        )

        target = self._resolve_out_dir(out_dir)
        try:
            path, digest = self._packer.pack(agent_dir, target, manifest)
        except Exception as e:  # noqa: BLE001 — every failure here is a user-facing outcome
            return PackageResult(False, f"packing '{agent_id}' failed ({type(e).__name__}): {e}")

        private = self._packer.private_plugin_ids(agent_dir)
        return PackageResult(
            ok=True,
            message=self._describe(manifest, path, digest, private, report),
            path=str(path),
            sha256=digest,
            size=path.stat().st_size,
            bundle_id=manifest.id,
            version=manifest.version,
            private_plugins=private,
        )

    def _resolve_out_dir(self, out_dir: str):
        from pathlib import Path

        return Path(out_dir).expanduser() if str(out_dir or "").strip() else self._packer.default_out_dir()

    @staticmethod
    def _describe(manifest, path, digest, private, report) -> str:
        lines = [
            f"Packaged '{manifest.id}' v{manifest.version} -> {path}",
            f"  {path.stat().st_size:,} bytes | sha256 {digest}",
        ]
        if private:
            lines.append(
                f"  ships {len(private)} private plugin(s): {', '.join(private)} "
                f"(inside agent/, untrusted once installed elsewhere)"
            )
        if manifest.plugins:
            lines.append(
                "  shared plugin deps: "
                + ", ".join(f"{d.id} ({d.source})" for d in manifest.plugins)
            )
        counts = report.counts()
        advisory = ", ".join(
            f"{counts[lvl]} {lvl}" for lvl in ("warn", "info") if counts.get(lvl)
        )
        if advisory:
            lines.append(f"  validation passed with {advisory} — see validate_agent for detail")
        lines += [
            "",
            "Anyone can install this with marketplace.install, or point the desktop installer "
            "build at it to produce a standalone .exe.",
            "Bump `version` in agent.toml before packaging a change — installs supersede BY "
            "VERSION, so re-packing the same number will not replace an existing copy.",
        ]
        return "\n".join(lines)
