# Registry admin manual — approving the people who publish

Who this is for: anyone who administers the marketplace — approves new creators, revokes bad
ones. You do NOT need AWS access, the root key, or the operator's machine. You need an admin
account and the CLI.

How the trust works, in four lines:

- every creator signs their own agents with their own key (minted for them, kept in the cloud)
- clients only trust keys named on the **roster**
- the roster is only valid when signed by the **platform root key**
- the root key lives KMS-wrapped in a vault; the publish service signs with it when an admin
  says so. **Admitting = telling the service to put a creator's key on that roster.**

A creator's FIRST publish returns 202 and their package is **parked** (held privately). Your
`admit` puts them on the roster AND publishes what they parked — they never publish twice.

---

## One-time setup (operator)

### 1. Name the admins

`infra/environments/<env>/dev.auto.tfvars` (gitignored — create it from the `.example`):

```hcl
publish_admin_identities = ["you@example.com", "teammate@example.com"]
```

```powershell
terraform -chdir=infra\environments\dev apply
```

Emails and/or account ids of the PLATFORM ACCOUNTS admins sign in with. Case-insensitive.
**Empty list = the admin door refuses everyone** (fail-closed). Being an admin grants ONLY
admit/revoke/pending — it changes nothing about publishing.

### 2. Vault the root key

Once, on the machine that holds the offline keypair file:

```powershell
cd v2
$env:AGENTD_CREATORS_TABLE = (terraform -chdir=infra\environments\dev output -raw publish_creators_table)
$KMS = terraform -chdir=infra\environments\dev output -raw publish_kms_key
python -m agent_runtime.cli.main bundle roster upload-root --root-key <keypair>.json --kms-key $KMS
```

The private half is stored KMS-encrypted; every use is IAM-gated and lands in CloudTrail.
**KEEP the offline keypair file** (offline backup + password manager). The vault holds a COPY;
the file is the recovery anchor. Losing BOTH means no roster can ever be re-signed — every
installed client pins that key, so recovery would mean rebuilding and re-shipping every
installer.

---

## Day-to-day (any admin, any machine)

Prerequisites: signed in to your admin account on this install. On a repo checkout also point
the CLI at the service (installed builds carry the URL baked in):

```powershell
$env:AGENTD_PUBLISH_TARGET = "<publish_url terraform output>"   # e.g. http://…:4300
```

### See who is waiting

```powershell
python -m agent_runtime.cli.main bundle roster pending
```

Lists every creator awaiting admission and the parked agents that will go live if you approve.

### Approve

```powershell
python -m agent_runtime.cli.main bundle roster admit                 # everyone waiting
python -m agent_runtime.cli.main bundle roster admit --id c-15ceb…   # one creator (repeatable)
python -m agent_runtime.cli.main bundle roster admit --dry-run       # show, change nothing
```

One call does the whole thing, in the only safe order: roster re-signed and written → creator
marked listed → their parked packages published. Success looks like:

```
admitted 1 creator(s). Published from parking: expense-summarizer 1.0.1.
```

Admission is once PER CREATOR, not per agent — everything they publish afterwards lists
automatically, signed with the key you just approved.

**What to check before admitting** (today this is the marketplace's ONLY review gate — there is
no content scan yet): do you recognise the email? does the parked agent look like a plausible
first publish and not a squat on a known name?

### Revoke

```powershell
python -m agent_runtime.cli.main bundle roster revoke --id c-15ceb…
```

Every client refuses everything they signed on its next index fetch, fail-closed. Their roster
row is kept (marked revoked) for the audit trail. This stops NEW installs and updates — it is
not a remote uninstall of copies already on machines.

---

## When it refuses

| response | meaning | fix |
|---|---|---|
| `401 not signed in` | no/expired session token | sign in on this install |
| `403 not a registry admin` | account not on the allowlist | add to `publish_admin_identities`, apply |
| `404 no route` | the Lambda predates the admin routes | deploy the current image (PUBLISH-SERVICE.md) |
| `no publish service to talk to` | CLI has no target | set `AGENTD_PUBLISH_TARGET` (see above) |
| `the root key vault is empty` | `upload-root` never ran | operator setup step 2 |
| `503 another publish is in progress` | index lock contention | retry in a moment |

## The offline fallback

The pre-vault flow still exists and is unchanged, for operators who keep the root key off the
cloud entirely: pass `--root-key <keypair>.json` to `admit`/`revoke` to sign the roster file
locally, then push it with `bundle roster publish --file registry-roster.json --to s3://<bucket>`.
The presence of `--root-key` is the mode switch. Note the vaulted and offline flows both edit
the same roster — use one or the other per change, not both at once.

## Security model, honestly

- an admin's laptop holds a session token and nothing else worth stealing
- the root private key exists in plaintext only inside the Lambda's memory during a signing call
- the trade made knowingly: an attacker inside the AWS account can sign rosters while inside
  (the offline file was immune to that; it is why you keep it as the anchor)
- admission is the only gate between "anyone with an account" and "installers signed with the
  platform certificate on strangers' machines" — treat the allowlist accordingly
