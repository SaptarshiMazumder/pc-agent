# Clean machine — stop every agentd and test installs like a stranger

Why this exists: ONE daemon serves the whole machine, so a dev daemon left running makes an
installer test lie to you — the installed app attaches to your dev process (your agents, your
config) instead of proving the install works. And a half-removed engine is exactly the state
that produced "port 8787 in use" + a dead rendezvous file. Installs only prove anything on a
machine that is actually clean.

## What can be running

| thing | what it looks like in Task Manager | where it came from |
|---|---|---|
| dev daemon | `python … -m agent_runtime` | `npm run dev`, `agentd serve`, a test |
| dev shell | `electron` (path inside the repo) | `npm run dev` |
| installed engine shell | `agentd.exe` | the core installer / a product stub |
| engine's daemon | `python.exe` under the engine's `resources\python` | spawned by `agentd.exe` |
| product app | `agentd.exe --app-dir …\apps\<id>` | a per-agent installer |

## Stop everything (safe to re-run any time)

**1. Close the dev shells first.** `Ctrl+C` any `npm run dev` terminals. Kill processes while a
shell is alive and its auto-reconnect spawns a fresh daemon behind your back.

**2. Kill every agentd process** — matched by COMMAND LINE, so unrelated python/electron
processes are untouched:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'agent_runtime|\\agentd\\|agentd\.exe' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
```

**3. Verify nothing is left and the port is free:**

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'agent_runtime|\\agentd\\' } |
  Select-Object ProcessId, CommandLine
Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue
```

Both must print nothing. If the port is still held, the owner is
`(Get-NetTCPConnection -LocalPort 8787 -State Listen).OwningProcess` — kill that pid.

Stopping here is enough to RUN an installer test. Continue below to test as a true stranger.

## Full reset — become Sally

⚠️ Step 6 deletes `~\.agentd`: sign-in state, marketplace-installed agents, sessions, memory of
every INSTALLED copy. The repo's own dev state (`<repo>\v2\.agentd`) is a different directory
and is not touched. Your authored agents live in the repo and are safe.

**4. Uninstall the products** — Settings → Installed apps:
- every per-agent app (e.g. "Expense Summarizer") — removes its payload, shortcuts, ARP row
- **agentd** (the engine). If it lives in `C:\Program Files`, expect an admin prompt — that is
  the broken all-users install from the bare-`/S` era; removing it is the point.

**5. Sweep what uninstallers can leave behind:**

```powershell
Remove-Item "$env:LOCALAPPDATA\agentd" -Recurse -Force -ErrorAction SilentlyContinue   # app payloads
Remove-Item "$env:LOCALAPPDATA\Programs\agentd" -Recurse -Force -ErrorAction SilentlyContinue # per-user engine files
Remove-Item HKCU:\Software\agentd -Recurse -Force -ErrorAction SilentlyContinue        # engine discovery key
```

**6. Wipe user state (read the warning above):**

```powershell
Remove-Item "$env:USERPROFILE\.agentd" -Recurse -Force
```

**7. Verify fresh — all four must be False/empty:**

```powershell
Test-Path "$env:USERPROFILE\.agentd"
Test-Path "$env:LOCALAPPDATA\agentd"
Test-Path 'HKCU:\Software\agentd'
Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue
```

Now run the installer you are testing. First agent install downloads the engine once (minutes);
any later agent installs in seconds — if it does not, the engine failed to register
(`HKCU:\Software\agentd\Engine` should exist after the first install).

## After the test

Nothing to restore for dev — `npm run dev` / `agentd serve` rebuild their state on next start.
You will need to sign in again anywhere sign-in is used (step 6 removed the session).
