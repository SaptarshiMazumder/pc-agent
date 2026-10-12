# Verification, 2026-10-12: production only

- Python compilation and Ruff pass; 30 lifecycle tests pass.
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

**Production is currently OFF/unprovisioned; its power controller is not installed.**
The production scripts are prepared, not evidence of a running production app.
The original production Terraform state bucket is missing, so a blind Terraform
apply is unsafe. Production state must be recovered/imported or provisioning
explicitly rebuilt before enabling its power controls.
