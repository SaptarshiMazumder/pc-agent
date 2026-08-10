# Publish service — command card (you run every command)

**Goal state:** Bob installs the desktop app, builds an agent with Agent Builder, clicks **Publish**,
and it appears in the marketplace — bundle *and* a ~120 KB installer. Sally, who has never heard of
agentd, downloads that installer and uses Bob's app. Bob needs no signing key, no AWS account, and
no build toolchain.

Sibling of `LAUNCH.md`, which brings up the platform itself. This card only adds publishing.

> **Still HTTP-only.** Same phase note as LAUNCH.md: treat this as private testing until a domain
> and an ACM cert are in place. The publish endpoint carries a session token, so it wants TLS before
> anyone outside the team touches it.

---

> **Paths below are placeholders.** `<...>` means substitute your own — this card is committed and
> read on other machines, so it names no real directory. The two you need are the registry you
> publish to and the keypair from `agentd bundle keygen`; both are settings, and both default to
> EMPTY in code so an install that has not been told cannot publish anywhere.

## What already works with no AWS at all

Publishing to a **local directory** exercises everything except the service. Do this first — it is
the fastest way to see the artifacts and catch a broken agent.

The obvious target is the local registry this install already reads, so a publish lands in the
store you can immediately browse — no new directory to invent:

```powershell
cd v2
..\.venv\Scripts\python.exe -c "from agent_runtime.config import load_config; print(load_config().registry_url)"
```

```ini
# v2/.env   (gitignored — these are YOUR machine's settings, not repo content)
AGENTD_PUBLISH_TARGET=<that path, or any directory>
AGENTD_PUBLISHER_KEYFILE=<path to your keypair from `agentd bundle keygen`>
```

Then in Agent Builder: **Publish** → preview → confirm. Or from a shell:

```powershell
..\.venv\Scripts\python.exe -m agent_runtime.cli.main bundle publish agents\game-master `
  --to <registry-dir> --unsigned
```

Success looks like: `index: 1 bundle(s) — game-master 0.3.0`, and a
`game-master-0.3.0-setup.exe` next to it if NSIS is available.

No keypair yet?

```powershell
..\.venv\Scripts\python.exe -m agent_runtime.cli.main bundle keygen --out <path>\publisher-key.json
```

Keep it outside the repo. It is the key every installed client pins; re-signing with a different
one makes every already-installed client reject the whole registry.

---

## 0. Publish the ENGINE once

A per-agent installer is a stub that downloads the shared engine, so the registry has to list one.
Without this every publish still works and simply reports "no installer" — which means nobody
without agentd can install anything.

```powershell
cd v2\clients\desktop
npm run dist:core                      # builds the engine installer (~250 MB)
```

```powershell
cd v2
# Found, not typed: the version is in the filename and changes every release.
$ENGINE = (Get-ChildItem clients\desktop\dist\core\*.exe | Sort-Object LastWriteTime -Desc)[0].FullName

..\.venv\Scripts\python.exe -m agent_runtime.cli.main bundle publish `
  --engine "$ENGINE" --to s3://<registry-bucket> --key <your-keypair>.json
```

**Success:** `engine: win <version>  (per-agent stubs install this)`. The version is read out of the
filename, so a mis-typed one cannot end up in the index.

Verify the index carries it:

```powershell
..\.venv\Scripts\python.exe -m agent_runtime.cli.main product engine
```

**Success:** a `url` and a 64-char `sha256`. If `sha256` is missing, no stub will ever be built —
that is deliberate; a stub must be able to verify what it runs.

---

## 1. First apply — repo, tables, key (no function yet)

The Lambda is an image-based function, so it cannot be created before its image exists. Leaving
`publish_image_tag` empty means this apply creates everything *around* the function.

```powershell
cd v2\infra\environments\dev
terraform init
terraform apply
```

**Success:** `publish_ecr_repository` is in the outputs; `publish_url` is empty.

```powershell
terraform output publish_ecr_repository
terraform output publish_creators_table
terraform output registry_bucket
```

## 2. Build and push the image

Build context is `v2/` — the image needs the `agent_runtime` package, not just the service folder.

```powershell
cd v2
# Both DERIVED, so this block is the same on any machine and in any environment.
$REPO   = (terraform -chdir=infra\environments\dev output -raw publish_ecr_repository)
$REGION = ($REPO -split '\.')[3]        # <acct>.dkr.ecr.<region>.amazonaws.com/...

aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin ($REPO -split '/')[0]
docker build -t "${REPO}:v1" -f services\publish\Dockerfile .
docker push "${REPO}:v1"
```

**Success:** the build prints an NSIS version — the Dockerfile asserts `makensis` and the stub
template are present, so a missing toolchain fails the *build* rather than the first publish.

## 3. Second apply — the function goes live

```powershell
cd v2\infra\environments\dev
terraform apply -var publish_image_tag=v1
```

Put `publish_image_tag = "v1"` in your tfvars so it sticks.

**Success:**

```powershell
terraform output publish_url        # e.g. http://agentd-dev-xxxx.elb.amazonaws.com:4300
```

Smoke-test that it is reachable and refusing correctly:

```powershell
curl -i -X POST "$(terraform output -raw publish_url)/registry/publish"
```

**Success:** `400` with `expected a multipart/form-data body containing a package file`. A `502` or a
timeout means the function is not wired to the target group; check the Lambda's CloudWatch log group.

---

## 4. Bake the URL into the build — the author configures NOTHING

An author installs the app, signs in, and presses Publish. They never open a `.env` and have no
idea a publish service exists. So the URL travels the same way `accounts_url` does: it is part of
the product.

```powershell
cd v2\clients\desktop
npm run sync:urls                # reads terraform outputs -> flavors' distribution.toml
npm run dist:core                # the engine, now carrying store.publish_url
```

Check it landed:

```powershell
Select-String publish_url flavors\core\distribution.toml
```

**Success:** `publish_url = "http://…:4300"`. If it is still `""`, the Lambda is not deployed —
`publish_url` is an empty terraform output until `publish_image_tag` is set, and the sync skips
empty values rather than blanking a good one.

Resolution order, so an operator can still override a shipped build:

```
AGENTD_PUBLISH_TARGET  >  config.json  >  store.publish_url in the build
```

The author's side of that is the third tier and needs no action. For a LOCAL test on your own
machine, the first tier is how you point at a directory instead.

Then: install the build (or `npm run dev`), sign in, Agent Builder → **Publish**.

**Expected first result: `202`** —

> accepted, awaiting review. Your first publish admits you to the creator roster. Your upload of
> `<agent> <version>` is held and will be published automatically the moment you are approved…

That is not a failure, and the author is DONE — the package is parked (privately, under the
registry bucket's `pending/` prefix, which the bucket policy keeps out of the public grant).
Admission completes their publish for them.

## 5. Admit the creator (any admin, once per author)

> The full admin manual — day-to-day approval/revocation, troubleshooting, the security model —
> is **[REGISTRY-ADMIN.md](REGISTRY-ADMIN.md)**. This section is just the first-time bring-up.

Admission is a SERVICE call, so it works from any machine and any admin — no root-key file on a
laptop in the loop. Two one-time setup steps first:

**(a) Name your admins** — `infra\environments\dev\dev.auto.tfvars`:

```hcl
publish_admin_identities = ["<your-account-email>"]
```

then `terraform apply`. Empty list = the admin door refuses everyone, fail-closed.

**(b) Vault the root key** — run once, on the machine that holds the offline keypair file:

```powershell
cd v2
$env:AGENTD_CREATORS_TABLE = (terraform -chdir=infra\environments\dev output -raw publish_creators_table)
..\.venv\Scripts\python.exe -m agent_runtime.cli.main bundle roster upload-root `
  --root-key <your-keypair>.json `
  --kms-key (terraform -chdir=infra\environments\dev output -raw publish_kms_key)
```

The private half lands KMS-wrapped; every decrypt is IAM-gated and in CloudTrail. KEEP the offline
file — the vault holds a copy, and that file is your recovery anchor if the account is ever lost.

Then the review step is two commands, from anywhere, signed in as an admin:

```powershell
..\.venv\Scripts\python.exe -m agent_runtime.cli.main bundle roster pending
..\.venv\Scripts\python.exe -m agent_runtime.cli.main bundle roster admit
```

**Success:** `admitted 1 creator(s). Published from parking: <agent> <version>.` One call did all
of it, in the only safe order: roster re-signed and written → creator flipped to listed → their
parked upload published. There is no separate `roster publish` step on this path.

(The OFFLINE flow still exists for operators who keep the root key off the cloud entirely: pass
`--root-key <file>` to `admit`/`revoke` and the old sign-locally-then-`roster publish` behaviour
is unchanged. Revoking works the same two ways: `bundle roster revoke --id <creator>` remotely, or
with `--root-key` locally.)

## 6. Verify the listing

The author does nothing further — their 202 upload is now live.

Verify as a stranger would:

```powershell
curl -s "https://<registry-bucket>.s3.<region>.amazonaws.com/index.json" | ConvertFrom-Json | `
  Select-Object -ExpandProperty bundles | Format-List id, version, publisher_id, installers
```

**Success:** the new bundle, with `publisher_id` set and an `installers` row carrying its own `sig`.

## 7. Be Sally

Download the installer URL in a browser, on a machine with **no agentd**. Run it. (Testing on
your own dev machine? Clean it first — **[CLEAN-MACHINE.md](CLEAN-MACHINE.md)** — or the app will
quietly attach to your dev daemon and the test proves nothing.)

**Or don't download anything.** An author whose agent.toml declares web delivery:

```toml
[delivery]
web = true        # store card gains "Open in browser" (requires [app])
exe = false       # optional: skip the installer entirely
```

publishes exactly the same way, and the store card links `<web host>/apps/<id>/` — the hosted
daemon installs the bundle from the registry on the FIRST visit (signature-verified, sandboxed,
web-opt-in only) and serves the app to every visitor after that. The link host comes from the
index's `web.host`, stamped by this service from its `WEB_HOST` env (terraform derives it from
the daemon's public url; nothing to configure by hand).

**Success:** it fetches the engine once, verifies its sha256, installs it silently, and puts the
agent's own name in the Start menu. Clicking it opens the author's UI.

**Expected:** a SmartScreen warning, until the code-signing certificate is bought. Nothing else about
the flow changes when it is — one certificate covers every author's installer forever, because the
service compiles and signs all of them.

---

## Rolling out a change

```powershell
cd v2
docker build -t "${REPO}:v2" -f services\publish\Dockerfile . ; docker push "${REPO}:v2"
cd infra\environments\dev ; terraform apply -var publish_image_tag=v2
```

## Before this opens to strangers

Deliberately not built yet — listed so the gap is a decision, not an oversight:

- **A scan/review gate.** The service signs every installer with your certificate, so malware
  inherits your reputation and can burn it. Today's roster admission is the only gate.
- **A per-creator publish quota.** Nothing rate-limits an authenticated author.
- **TLS.** A session token over HTTP.
- **`create_tool` in hosted mode.** Authoring tools reachable by any signed-in account on a shared
  runtime is remote code execution on that container. Gate it off, or give each account its own
  runtime, before publishing is public.

## Teardown

```powershell
terraform apply -var publish_image_tag=""    # removes the function, keeps repo/tables/key
```

The KMS key has a 30-day deletion window, and the creators table has point-in-time recovery on: it
IS the creator identity, and losing it would orphan every published bundle from the key that signed
it, with no client ever verifying them again.
