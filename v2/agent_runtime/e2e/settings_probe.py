"""Settings round-trip probe — does Save actually SAVE, on a live daemon, as a real client?

Born from a live bug: the web settings page said "Saved." while the model dropdown reverted on
every reload. Reading the code said every branch works; this probe asks the daemon instead. It
drives the same two connections the product uses and checks the write is THERE afterwards:

  HOST round-trip    connect exactly like the shell (session token), config.set a per-agent
                     override (patch.agents.<id>.key), config.get it back, compare.
  SCOPED round-trip  connect exactly like an agent's own window (scope=agent:<id> + session),
                     send the SAME shape its settings screen sends, read it back, compare.
  DECLARED setting   when the agent declares [[settings]], write one through `keys` and check
                     presence comes back (the per-account settings store path).

Every step restores what it found, so probing production state is safe. Exit code 0 = every
step round-tripped; 1 = at least one save lied. The output names the exact step and payload,
which is the point — "saving is broken" becomes "THIS write on THIS connection came back as
THAT".

    python -m agent_runtime.e2e.settings_probe \
      --daemon wss://staging.example:8787 --token <account-session-token> --agent news-flash
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .live_driver import WsGatewayTransport

PROBE_VALUE = "probe-model-roundtrip"


class Probe:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str) -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            self.failures.append(name)


async def _roundtrip(probe: Probe, label: str, url: str, token: str, agent: str,
                     scoped: bool) -> None:
    """One connection kind, three steps: read, write, read-back (then restore)."""
    conn = url
    if scoped:
        sep = "&" if "?" in conn else "?"
        conn = f"{conn}{sep}scope=agent:{agent}"
    print(f"\n== {label} ==")
    try:
        async with WsGatewayTransport(conn, token) as t:
            before = await t.call("config.get", {})
            values = before.get("values") or {}
            original = ((values.get("agents") or {}).get(agent) or {}).get("model")
            probe.check(f"{label}: config.get", True,
                        f"agents.{agent}.model is {original!r} before")

            merged = {**((values.get("agents") or {}).get(agent) or {}), "model": PROBE_VALUE}
            res = await t.call("config.set", {"agentId": agent,
                                              "patch": {"agents": {agent: merged}}})
            saved = bool(res.get("saved"))
            probe.check(f"{label}: config.set answered saved=true", saved,
                        json.dumps({k: res[k] for k in res if k != "path"})[:300])

            after = await t.call("config.get", {})
            got = (((after.get("values") or {}).get("agents") or {}).get(agent) or {}).get("model")
            probe.check(f"{label}: value visible after save", got == PROBE_VALUE,
                        f"read back {got!r} (wanted {PROBE_VALUE!r})"
                        + ("" if got == PROBE_VALUE else " — THE SAVE LIED"))

            # restore what was there (delete the probe value by writing the original back,
            # or the block without `model` when there was none)
            restored = {**((((after.get("values") or {}).get("agents") or {}).get(agent)) or {})}
            if original is None:
                restored.pop("model", None)
            else:
                restored["model"] = original
            await t.call("config.set", {"agentId": agent, "patch": {"agents": {agent: restored}}})
            final = await t.call("config.get", {})
            back = (((final.get("values") or {}).get("agents") or {}).get(agent) or {}).get("model")
            probe.check(f"{label}: restored", back == original,
                        f"agents.{agent}.model is {back!r} again")
    except Exception as e:  # noqa: BLE001 — a dead connection is the probe's answer, not a crash
        probe.check(f"{label}: connection", False, f"{type(e).__name__}: {e}")


async def _declared_setting(probe: Probe, url: str, token: str, agent: str) -> None:
    """If the agent declares [[settings]], round-trip one through `keys` (the settings store)."""
    print("\n== declared [[settings]] via keys ==")
    try:
        sep = "&" if "?" in url else "?"
        async with WsGatewayTransport(f"{url}{sep}scope=agent:{agent}", token) as t:
            cfg = await t.call("config.get", {})
            declared = [d.get("key") for d in (cfg.get("settings") or []) if d.get("key")]
            if not declared:
                probe.check("declared: (skipped)", True, f"{agent} declares no [[settings]]")
                return
            key = declared[0]
            was_set = bool((cfg.get("env") or {}).get(key))
            if was_set:
                # Never overwrite a value we cannot read back to restore. Presence-only probe.
                probe.check("declared: (skipped)", True,
                            f"{key} already has a value — not overwriting it")
                return
            res = await t.call("config.set", {"agentId": agent, "keys": {key: PROBE_VALUE}})
            probe.check("declared: config.set", bool(res.get("saved")), json.dumps(res)[:200])
            after = await t.call("config.get", {})
            present = bool((after.get("env") or {}).get(key))
            probe.check("declared: presence after save", present,
                        f"env[{key}] -> {present}")
            await t.call("config.set", {"agentId": agent, "keys": {key: ""}})  # remove again
    except Exception as e:  # noqa: BLE001
        probe.check("declared: connection", False, f"{type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Round-trip config.set/config.get on a live daemon.")
    ap.add_argument("--daemon", required=True, help="ws URL, e.g. wss://staging.example:8787")
    ap.add_argument("--token", required=True, help="account session token")
    ap.add_argument("--agent", required=True, help="agent id whose per-agent override to probe")
    args = ap.parse_args(argv)

    probe = Probe()

    async def run() -> None:
        await _roundtrip(probe, "HOST connection (the shell)", args.daemon, args.token,
                         args.agent, scoped=False)
        await _roundtrip(probe, f"SCOPED connection (the {args.agent} window)", args.daemon,
                        args.token, args.agent, scoped=True)
        await _declared_setting(probe, args.daemon, args.token, args.agent)

    asyncio.run(run())
    print()
    if probe.failures:
        print(f"BROKEN: {len(probe.failures)} step(s) failed -> {', '.join(probe.failures)}")
        return 1
    print("all settings round-trips held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
