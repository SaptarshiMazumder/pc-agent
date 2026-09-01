# The admin console — command card (you run every command)

**Goal state:** you sign in to the web app with your normal account, an **Admin** entry appears in
the menu, and from it you can see and change every user, agent, creator, product and key on the
platform — with no CLI, no AWS console, and no second credential.

Sibling of `LAUNCH.md` (the platform), `PUBLISH-SERVICE.md` (how agents get in) and
`REGISTRY-ADMIN.md` (approving creators, which this page now does with buttons).

> **Still HTTP-only.** Same phase note as the rest: an admin session over plain HTTP is a session
> anyone on the path can take, and it is a session that can now change platform keys. Treat this as
> private testing until TLS is on.

---

## What it is

One new surface, `/admin/*` on the **accounts** service, and one page in the app that talks to it.

Accounts hosts it because accounts owns identity, and "is this account an admin" is an identity
question. Everything else the console shows is either already in that database (users, credits,
usage, products, the ledger, sign-ins) or is fetched **server-side** by it — the registry index for
agents, and the publish service for creators. That is why the browser only ever talks to one
origin: no CORS on the Lambda, no mixed-content problem when TLS lands on one port before another.

## The three tiers, and which one you are

| Tier | Who | Where it lives | Demotable in the console? |
| --- | --- | --- | --- |
| **Break-glass admin** | `publish_admin_identities` | terraform | **No** — by design |
| **Roster admin** | promoted in the console | `admins` table, accounts DB | Yes |
| **Everyone else** | — | — | — |

The config tier cannot be demoted from the dashboard on purpose: one mis-click must not be able to
leave a deployment with no administrator and no way back in that does not involve editing SQLite on
EFS by hand. The console shows which tier an account is in and says so when it refuses.

An **empty** list plus an **empty** table means nobody, fail-closed — the same rule the creator
roster already follows.

## 1. Name yourself

`infra/environments/dev/dev.auto.tfvars` (gitignored):

```hcl
publish_admin_identities = ["you@example.com"]
```

One list, read by **both** services now: the publish Lambda for creator admission, and accounts for
the console. There is no second variable to keep in step.

```powershell
terraform -chdir=v2\infra\environments\dev apply
```

**Expect in the plan:** a new task definition for **accounts** (it gains the admin environment and a
new task role), a second IAM role `agentd-dev-ecs-task-admin` with three policies, and nothing else.
No ALB, no EFS, no other service. If you see a resource being *destroyed*, stop and read it.

## 2. Ship the code

`v2/accounts/**` is already in the deploy workflow's path filter, so this is the whole step:

```powershell
git push origin develop
```

Watch the workflow's "changed" step actually list `accounts`.

## 3. Open it

Sign in to the web app as the account you named. **Admin** appears in the menu next to Settings.

```powershell
# or check the API directly
$P = terraform -chdir=v2\infra\environments\dev output -raw platform_url
curl.exe -H "Authorization: Bearer <your access token>" "$P/admin/whoami"
```

**Success:** `{"is_admin":true,"source":"config"}`. `false` with `source:""` means the identity in
the tfvars does not match your account's email or id — it is matched case-insensitively against
both, so check for a typo rather than a casing problem.

---

## What each panel does

| Panel | Reads | Can change |
| --- | --- | --- |
| **Overview** | this month's usage, accounts, credits | — |
| **Users** | every account, its budget, balance, agents, devices, sign-ins | grant credits, set/clear the monthly cap, enable/disable, sign out everywhere, grant agent access, promote/demote admins |
| **Agents** | the signed registry index — versions, creators, delivery, engine row | — |
| **Creators** | every creator and their state, plus what they parked | admit, revoke |
| **Money** | products, subscribers, the double-entry ledger and its balance check | list/withdraw a product |
| **Keys** | signing keys, platform + provider secrets, creator keys | rotate the signing key, replace a secret |

### Three behaviours worth knowing before you click

**Granting credits puts an account on a metered plan, permanently.** The proxy enforces credits
only for accounts that have *ever* been granted them; before the first grant an account is on the
free tier and a zero balance does not stop it. After it, zero means refused. That is usually what
you want — it is just not reversible by taking the credits away.

**Signing out everywhere is not instant.** It kills refresh tokens, so no *new* access token can be
minted, but tokens already issued live out their remaining minutes. The console tells you the exact
window. Pair it with **Disable** when it has to be immediate — that *is* instant, because the
account row is re-read on every call.

**Replacing a secret restarts things.** ECS injects secrets when a container starts, so writing a
new provider key and stopping there leaves every service on the old value with no error anywhere.
The console rolls the services that read that key as part of the action and reports which ones. It
knows which because the map is derived from the services map itself (`AGENTD_KEY_CONSUMERS`), so
adding a secret to any service makes the rollout correct with no edit here.

## What it deliberately cannot do

- **Show a secret's value.** Only whether it is set, when it changed, and who reads it. A page that
  can display `GEMINI_API_KEY` is a page that can leak it.
- **Read a private signing key.** The creator panel reports that a key exists and is KMS-wrapped;
  the wrapped bytes never leave the publish service.
- **Unlist a bundle.** Still `agentd bundle unlist`, deliberately: it is the one action here whose
  artifact deletion has no undo.
- **Delete an account.** Disable is reversible and keeps the ledger intact; deletion would orphan
  money history that cannot be reconstructed.

## The trade this makes, stated plainly

The accounts task now wears a role that can read and write this environment's secrets and start a
rollout. That makes it the most valuable target in the deployment, and it is the price of setting a
key from a browser instead of a terminal. It is bounded three ways: a **separate** task role so no
other container inherits it (the daemon runs third-party agent code and must never hold this), ARNs
scoped to this environment only, and no create or delete verbs anywhere in the policy.

If that trade is not one you want, set the panel to read-only by removing the
`aws_iam_role_policy.task_admin_plane` statements for `PutSecretValue` and `ecs:UpdateService`. The
console degrades honestly — it reports what it cannot do rather than failing.

## When it refuses

| Response | Meaning | Fix |
| --- | --- | --- |
| `401` | no token, or it expired | sign in again |
| `403 not a platform admin` | you are signed in and not on either list | add to `publish_admin_identities`, apply |
| `409 … deploy configuration` | tried to demote a break-glass admin | remove them from the tfvars and apply |
| `400 you cannot disable your own account` | the lockout guard | ask another admin |
| `503 no publish service is configured` | `publish_image_tag` is empty | see PUBLISH-SERVICE.md |
| keys panel says "not configured" | running locally with no AWS | expected — those panels need the deployed role |

## Teardown

Remove `admin_plane = true` from the accounts entry in `modules/variables.tf` and apply: the service
goes back to the shared task role and the console's key panels degrade to "not configured". The
`/admin/*` routes stay — they need no AWS, and user/creator/money management keeps working.
