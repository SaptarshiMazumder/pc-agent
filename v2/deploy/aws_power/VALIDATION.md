# Verification, 2026-10-13: production only

## Production restarted, 2026-10-16

- User explicitly requested full production ON. The initial attempt failed before starting
  compute: the power role applied new-endpoint request-tag conditions to existing VPC,
  subnet and security-group resources, so `CreateVpcEndpoint` was denied.
- The installer now separates those resource authorizations. Existing network dependencies
  are restricted to exact production ARNs; endpoint creation requires production ownership
  tags and the saved AWS service names. The live power-role policy was repaired with the
  same helper. IAM simulation allows the saved network and tagged endpoint, and denies an
  unrelated VPC and staging endpoints. 43 power-controller/installer tests and Ruff pass.
- The manual ON then completed. All six ECS services are at desired/running/pending 1/1/0
  with completed deployments. Background Lambda concurrency and application schedules are
  restored. Public web/accounts return 200, and public DNS points to the restored ALB.
- Nightly mode remains automatic; the 02:00 Asia/Tokyo OFF schedule remains enabled with no
  automatic ON. Staging was not started. The local pause flag is synchronized to ON.
- ON recreates the ALB/listener/interface-endpoint IDs outside Terraform. Reconcile those
  resource IDs before a subsequent Terraform apply; do not blindly apply stale saved plans.

## Individual welcome credits deployed, 2026-10-13

- Accounts and web now use immutable image `29a9d04c937853a2db60ff639c240ef2317af41b`.
- Live Neon policy: enabled, 100,000 promotional personal credits once per new account,
  expiry 30 days. Admin → Money controls enablement, amount and expiry without redeployment;
  cancelling edits does not save, disabling only stops future grants. No existing user was
  backfilled and no production test user or promotional grant was created for verification.
- The user's selected existing account was added to the editable platform-admin roster.
- 18 welcome-credit tests pass on SQLite and on isolated Neon schemas, including parallel
  claims, Google first login, rollback, future-only edits and admin authorization. Desktop
  type checks and a hidden Electron UI test pass for load/save/disable/enable/cancel and
  native transport origin/method/header restrictions. Broader accounts/admin/ledger tests:
  111 pass; the existing keys-panel fixture test still expects an unconfigured secret while
  the shared fixture sets `tests/app-secret`. Unchanged HEAD has the same configuration result.
- The accounts image workflow built successfully, but its redundant old-image rollout waiter
  was cancelled: the one-host pool had only 256 CPU units free, below the duplicate accounts
  task's requirement. Terraform now uses stop-then-start deployments for pools capped at one
  EC2 host. Only accounts/web task definitions changed; ingest/model-proxy received deployment
  ordering settings only. All five EC2 services completed their deployments at 1/1/0.
- Public accounts/web health and hosted-origin admin CORS return 200; anonymous admin policy
  reads return 401. Production remains one host with ASG maximum 1. The live restore manifest
  was captured and the power mode remains automatic (OFF at 02:00 JST, manual ON).

- Ruff passes; 38 lifecycle tests pass, including endpoint ownership/recovery and
  ALB condition normalization regression tests.
- Script/installer defaults now target `agentd-production`, Mumbai (`ap-south-1`).
- Staging ON is rejected; staging installer and keep-on are rejected too.
- AWS deploy/redeploy/Lambda/registry workflow choices accept production only on
  the feature branch. They must be merged before GitHub's default workflows change.

## Staging retirement completed

The initial implementation wrongly targeted the remaining staging deployment.
It was shut down and the controls were removed after the user's correction:

- All five ECS services have desired/running/pending count zero; no active tasks.
- Both staging ASGs have min/max/desired zero and no instances.
- The former `t3.medium` instance is terminated; staging ALB is deleted.
- Tokyo has no remaining load balancers, Elastic IPs, staging NAT gateways, or VPC endpoints.
- Application schedules are disabled, worker Lambda reserved concurrency is zero,
  and ECS capacity-provider/service autoscaling is disabled while staging is OFF.
- The mistakenly installed staging power Lambda, nightly schedule, SSM mode, and
  its two IAM roles were removed. All 23 staging CloudWatch alarms were removed.
- Neon and durable AWS storage, images, secrets, certificates, and signing material
  were retained. Retained storage/DNS/secrets can still have small ongoing charges.

The live OFF caught an ECS cluster limitation: capacity-provider updates must run
sequentially. The implementation and regression test now enforce that. The local
Lambda invocation connection closed during shutdown, but the cloud operation
continued; AWS APIs and saved lifecycle state independently confirmed completion.

Automatic approval review rejected disabling all four shared AWS workflows because
that would also halt production deployments. Shared workflows remain enabled;
the safer production-only workflow edits are included on the feature branch.

## Production restored, then explicitly stopped for the day

The user requested shutdown on October 13 (Asia/Tokyo) and clarified **prod only**.
No staging resources were changed during this shutdown.

- Production was rebuilt in Mumbai, `ap-south-1`, account `891612568944`.
  Terraform state is again stored in the private versioned Tokyo bucket
  `agentd-tfstate-891612568944`, key `production/terraform.tfstate`.
- Cloudflare delegation now points `platform.nakamaai.co` to the recreated
  Route53 zone `Z03906613MU46UB1NOGY1`. The four trusted services were reachable
  before shutdown. Eight production images were built successfully with tag
  `d0c7319e7b9ea35b13d1bb2293d2ae8c8ddfdead`.
- The final Terraform apply completed: 33 additions, 14 changes, two ECS task
  definition replacements. It is no longer running, and no production release
  branch Actions runs remain active.
- All six production ECS services have desired/running/pending count zero.
  The one-host ASG has min/max/desired zero and no instances; host
  `i-038be43370f61215a` is terminated. Capacity-provider scaling and termination
  protection are disabled, and service autoscaling is suspended.
- Production ALB and all three paid interface endpoints were deleted, verified
  absent along with their endpoint ENIs; the free S3 gateway endpoint remains.
  No production NAT gateway,
  Elastic IP, or EBS volume was found in the shutdown verification.
- All production schedules are disabled, six application Lambdas have reserved
  concurrency zero, and 28 production metric alarms were removed. Production
  CloudFront distribution `EHA4FYF2Z256Y` is disabled.
- Local `paused.auto.tfvars` is set to true. No automatic ON is configured.
- Neon, main DynamoDB tables, EFS/S3/ECR contents, secrets, DNS, certificates,
  signing KMS key, and backups remain. These retained resources may still cost
  money; this shutdown does not make the whole account free.

The local ignored recovery snapshot is
`tmp/production-manual-off-recovery-20261013.json`. Terraform definitions and
remote state also preserve configuration. The direct shutdown creates Terraform
drift: reconcile/recreate deleted ALB/listeners and endpoints before a future
deployment. Do not blindly run the power ON script: the production power Lambda
and nightly 02:00 Asia/Tokyo scheduler **have not been installed yet**.

Pending work for the next explicitly authorized session:

- Complete and test the uncommitted endpoint lifecycle and asynchronous CLI
  changes; reconcile Terraform resource IDs after recreation.
- Recover the original registry signing roster from DynamoDB system backups.
  Original KMS key `d3c6bef3-fe80-4e43-a93d-030f308279d5` is enabled and retained.
  The GitHub keypair is a creator key, not the pinned root; root recovery job
  `38269946225` correctly refused to replace the pinned root. Its temporary IAM
  bootstrap policy was removed.
- Temporary restored tables suffixed `recovery-20261013` were removed only after
  confirming their original system backups remain AVAILABLE. Restore again from
  creators backup `01791601814779-00000000` and bundle-owners backup
  `01791601812887-00000000`; do not rotate the original root or creator keys.
- Install and verify cloud-side nightly OFF, manual ON/OFF, and persistent
  keep-on override, then validate the isolated agent runtime end to end.

## Production resumed on the user's request, October 13

- Production restart completed with no resource deletions. A transient local
  DNS lookup failure interrupted Terraform polling; the working ALB was verified
  active before clearing its erroneous taint. Remote state was recovered, and
  the remaining plan applied: 24 additions, 17 changes, zero deletions.
- All six ECS services have desired/running/pending counts `1/1/0`, and all five
  application target groups are healthy. Public HTTPS web, accounts `/health`,
  proxy `/health/liveliness`, ingest `/health`, and daemon `/healthz` return 200.
- Exactly one `m6a.large` host is InService: `i-0974cdf5928f64fbb`. The ASG has
  min/max/desired `1/1/1`; the egress proxy runs on Fargate.
- Private production power controller is installed. Nightly schedule
  `agentd-production-power-off-0200` is ENABLED, `cron(0 2 * * ? *)`, timezone
  `Asia/Tokyo`, with mode `automatic`. No automatic start schedule exists.
- Recovery manifest in private encrypted/versioned bucket
  `agentd-power-891612568944-ap-south-1` captures all six desired counts, three
  interface endpoints and two DNS aliases. Production-only IAM scopes apply.
- A live manual ON check found DescribeRules returning both legacy `Values`
  and typed host/path condition configs, which AWS rejects on write. The
  controller normalizes these, four regression cases pass, and the fixed
  controller's manual ON finished with state `on` and no added host.
- Staging was not changed. Neon, saved data, signing keys and backups are retained.

Nightly OFF is installed and tested through lifecycle tests and live configuration
inspection; a full production OFF/ON cycle was not performed during this requested
startup. Registry root/roster recovery and Terraform ID reconciliation after future
power recreations remain pending. Do not claim all publishing/payment features
are validated merely because application health checks pass.

## Registry and desktop billing investigation, October 13

- Production and staging registry buckets both contain zero objects; the missing
  public `index.json` yields HTTP 403 because anonymous callers cannot list the bucket.
- System backup copies of production creators and owners restored successfully,
  but scans found zero rows in both. These empty temporary copies were removed;
  original backups and main tables remain. They do not recover the pinned root.
- Local registries and `registry-roster.json` use different signing identities;
  they do not verify against the current desktop pin. Never upload them as trusted
  production data or change the pin without an explicit recovery/reset decision.
- Original signing-key backup location was requested from the user and remains
  required for preserving that identity. No matching signing setting was found
  in repository `.env` or `v2/.env`.
- A verification mistake invoked `RegistryClient._verify` on original generated
  cache files; it deletes mismatched artifacts. Both figure-creator copies were
  restored byte-for-byte from surviving copies. The game-master and presentation
  copies were regenerated from surviving payload/source; their old local indexes
  still describe the old signed bytes. Do not treat these regenerated copies as
  a verified signed restoration. Their source agents and index files were retained.
- Desktop billing directly fetched accounts from the renderer and was blocked
  by production CORS, leaving indefinite loading placeholders. A narrow native
  transport now supports only GET `/products`, GET `/me/credits`, and POST
  `/me/checkout` at the configured accounts origin, with no internal-key forwarding.
- Both TypeScript checks and desktop build pass. Fresh hidden Electron regression
  shows Google and loads six billing packs in 750ms while renderer platform
  requests are blocked. Authenticated native `/me/credits` returns 200 in ~2.1s.
- Accounts runs in Mumbai; unchanged Neon is in Singapore. No database move,
  live purchase or model call was performed during billing diagnosis.

## Approved registry identity reset

The user explicitly approved applying a replacement root and signed empty registry
to prod and the desktop dev build after the original key could not be found.

- Replacement root public key: `Op08JiRZ3W0N7/ezpCky0bw/Tbm1v0g22aagvm4i3k8=`.
- Private half exists as KMS ciphertext in the existing production root-vault row,
  encrypted with `d3c6bef3-fe80-4e43-a93d-030f308279d5`. Private encrypted backup:
  `s3://agentd-power-891612568944-ap-south-1/registry/root-key-backups/20261013.json`.
  No plaintext key was written or printed.
- Public `index.json` is now schema 2 with a signed empty roster and no bundles.
  The existing registry client fetched and verified it against the new pin;
  the old pin correctly refuses it. Public `catalog.json` returns 200.
- Production daemon and desktop core/studio pins are updated together. Original
  main tables, creator records, Neon and staging are unchanged. Old incompatible
  bundles were not published. Existing installers need a new build.
- Plain desktop `npm run dev` URL sync now defaults to production, preventing a
  subsequent restart from silently retargeting the desktop to old dev endpoints.
- Restart `npm run dev` to load the new main/preload transport and daemon pin.
  An empty store is expected until compatible bundles are published.

Desktop/SDK billing validation:

- The SDK and both Agent Builder UIs rebuilt with the billing fix; canonical
  template/vendor assets were refreshed by the standard SDK build script.
- The broader SDK suite has 20 passes and 14 failures in legacy session/token
  tests (including a removed `TokenManager` export). An isolated build of unchanged
  HEAD client/auth/billing source reproduces the same 20/14 result; these failures
  predate this fix. They were not hidden by restoring old credential storage.
