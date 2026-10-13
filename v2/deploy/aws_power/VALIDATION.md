# Verification, 2026-10-13: production only

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
