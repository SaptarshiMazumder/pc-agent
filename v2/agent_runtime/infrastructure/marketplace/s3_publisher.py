"""S3RegistryPublisher — the OPERATOR path, wrapping `agentd bundle publish` exactly.

ONE PUBLISHER, NOT TWO. This does not reimplement publishing; it calls the very function the CLI
calls (``bundle.run_publish``) with the same argument shape. The guards live in there and every one
of them is the residue of a real accident:

  * publishing rebuilds the WHOLE index, so prior entries are carried forward — otherwise releasing
    one agent silently unpublishes every other agent in the registry (and, since the engine block is
    carried the same way, breaks every per-agent installer built afterwards)
  * a key that does not match the registry's current publisher_key is REFUSED, because re-signing
    with a new key makes every already-installed client reject the whole registry
  * index.json uploads LAST, so a failed artifact upload leaves the registry untouched rather than
    advertising a download that 404s

A second implementation would have to re-earn all of that, and would drift the first time one side
was fixed.

WHO CAN USE THIS: whoever holds the registry's signing key and write access to its bucket. That is
us. An ordinary author publishes over HTTP instead (``HttpRegistryPublisher``) and needs neither.
"""

from __future__ import annotations

import argparse
import contextlib
import io
from pathlib import Path

from agent_runtime.application.interfaces.bundle_publisher import PublishRequest, PublishResult


class S3RegistryPublisher:
    def __init__(self, target: str, keyfile: str = ""):
        self._target = (target or "").strip()
        self._keyfile = (keyfile or "").strip()

    @property
    def name(self) -> str:
        return self._target or "(no target)"

    def requirements(self) -> list[str]:
        missing = []
        if not self._target:
            missing.append(
                'publish_target — where to publish, e.g. "s3://my-registry-bucket" or a local '
                "directory (env: AGENTD_PUBLISH_TARGET)"
            )
        if not self._keyfile:
            missing.append(
                "publisher_keyfile — path to the keypair from `agentd bundle keygen` "
                "(env: AGENTD_PUBLISHER_KEYFILE). Publishing to a signed registry without it would "
                "be refused anyway, because every installed client pins that key."
            )
        elif not Path(self._keyfile).is_file():
            missing.append(f"publisher_keyfile points at a file that does not exist: {self._keyfile}")
        return missing

    def publish(self, request: PublishRequest) -> PublishResult:
        from agent_runtime.cli.commands import bundle as bundle_cli

        args = argparse.Namespace(
            agent_dir=[str(request.agent_dir)],
            to=self._target,
            key=self._keyfile,
            name="",
            publisher="",
            version=request.version,
            rotate_key=False,  # a key rotation invalidates every installed client — CLI only
            unsigned=False,  # an unsigned public registry is never what a tool should choose
            dry_run=request.dry_run,
            publisher_id="",
            roster="",
            no_installers=not request.with_installer,
            engine="",  # publishing the ENGINE is a release job, never a per-agent publish
            engine_version="",
        )
        # run_publish reports through stdout (it is a CLI entry point). Captured so the tool returns
        # the same text a publisher would have read in a terminal — including the index listing,
        # which is the part worth checking before confirming.
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = bundle_cli.run_publish(args)
        output = buffer.getvalue().strip() or "(no output)"
        ok = code == 0
        return PublishResult(
            ok=ok,
            dry_run=request.dry_run,
            message=(
                "PREVIEW — nothing uploaded."
                if ok and request.dry_run
                else f"published to {self._target}"
                if ok
                else "publish FAILED"
            ),
            detail=output,
        )
