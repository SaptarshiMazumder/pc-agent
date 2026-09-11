# Figure Creator UI

Build from the repository root (uses the existing clients/canvas toolchain):

```powershell
node v2/agents/figure-creator/scripts/build-ui.mjs
```

This build writes **only** `figure-creator/ui/vendor/`. Do not use the shared canvas
vendor-all build for this product: it does not include Figure Creator's adapters.

The shared canvas shell, account/settings controls, and builder template's sign-in
and credits screens are imported unchanged. Local account/settings adapters add
the credits entry point and balance-change subscription. `page-sdk.ts` binds those
screens to the page SDK so authentication and credit notifications have one owner.
No payment endpoints, checkout handling, prices, or grants are implemented locally.

Payment flow: shared `Credits` → page SDK `BillingClient` → accounts `/me/checkout`.
The shared screen opens interactive checkout and waits for the server-side grant.
Closing the credits dialog leaves the chat and canvas mounted.

Run the contained payment tests from `v2/agents/figure-creator` (all network traffic
is intercepted). Keep this working directory so any Chromium diagnostic log also
stays inside the agent:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Resolve-Path ../..).Path
../../.venv/Scripts/python.exe -B -m pytest tests -q -p no:cacheprovider
```

Set `FIGURE_CREATOR_SCREENSHOTS=1` to capture the light, dark and narrow credits
layouts under this agent's ignored `.test-artifacts/` directory.
