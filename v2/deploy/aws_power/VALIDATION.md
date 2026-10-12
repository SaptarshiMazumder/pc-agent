# Verification, 2026-10-12

- Python compilation and Ruff pass.
- 28 lifecycle tests pass: manual-only ON, persistent override, manual OFF in either
  mode, stale event rejection, recovery snapshot protection, DNS matching and routing
  preservation, autoscaling suspension, and startup/shutdown ordering.
- Private Lambda `agentd-staging-power` deployed in account ending 8944, Tokyo.
- A live configuration snapshot was saved and read back in the encrypted, private,
  versioned recovery bucket before the shutdown schedule was enabled.
- The live `validate` action accepts all six saved listeners and their restoration
  requests against the AWS SDK input schemas.
- Live scheduled-OFF invocation with keep-on mode returned `skip-keep-on`.
- Live stale scheduled-OFF invocation returned `skip-stale`.
- Final mode is `automatic`; schedule is enabled with `cron(0 2 * * ? *)`, timezone
  `Asia/Tokyo`, and no flexible time window. There is no automatic ON schedule.
- Controller concurrency is one. Neither verification invocation stopped compute.
- 46 IAM permission simulations allow the captured shutdown/recovery operations,
  including both exact DNS records, autoscaling targets, listeners, and job roles.

Installation leaves current application capacity unchanged: accounts/model-proxy
are running; web/ingest/daemon already had desired count zero. These controls restore
those saved counts. Full application readiness is a separate deployment task.

A destructive shutdown/start cycle has not been run against the live application.
The first scheduled shutdown begins at the next 02:00 Tokyo time. Normal OFF preserves
Neon and durable AWS storage, but interrupts active jobs and removes ephemeral hosts.
