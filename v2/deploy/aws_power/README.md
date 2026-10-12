# AWS on demand

Run from the repository root with your existing AWS credentials:

```powershell
./v2/deploy/scripts/power.ps1 on          # manual start; wait for services
./v2/deploy/scripts/power.ps1 off         # manual shutdown, even in keep-on mode
./v2/deploy/scripts/power.ps1 keep-on     # persistently skip the nightly shutdown
./v2/deploy/scripts/power.ps1 automatic   # restore the nightly shutdown
./v2/deploy/scripts/power.ps1 status
```

The AWS schedule starts shutdown every day at **02:00 Asia/Tokyo** (within AWS's
one-minute scheduling precision). Your PC does not need to be on. There is no
automatic start schedule. `keep-on` and `automatic` only change the saved mode;
they do not start or stop anything. Manual OFF always works in either mode.
Change to keep-on before shutdown starts. Services take a few minutes to stop/start.

Shutdown stops ECS services and standalone tasks, disables application schedules
and worker Lambdas, disables ECS managed scaling, scales the existing host groups
to zero, and removes the application load balancer. It preserves Neon, EFS, S3,
DynamoDB, images, secrets, signing material, TLS certificates, and hosted zones.
Running jobs/browser sessions are interrupted. Instance-local caches are ephemeral.
Storage, DNS, retained secrets/keys, and monitoring can still incur charges while OFF.

ON recreates the load balancer and its listeners/rules/certificates, restores the
saved service counts and host capacity, and updates only the captured Route53
records to the new load balancer address. Existing custom hostnames stay the same.
It enables background schedules only after ECS deployments have completed.

These scripts target **only `agentd-production` in Mumbai (`ap-south-1`)**, matching
the repository's production infrastructure. Staging is retired and must remain OFF;
ON and keep-on commands reject it. The previous staging controller has been removed.
**Production is not yet provisioned and its power controller is not installed.**
Commands will fail until production resources and their controller are deployed;
they do not silently fall back to staging or create a second environment.
Once installed, controls restore saved service counts, including deliberate zeros.
Use `capture` after intentional production capacity changes while the recorded state is ON.

## Installation and recovery

```powershell
python -m pip install boto3
python v2/deploy/aws_power/install.py
```

Installation snapshots the live configuration into a private, encrypted, versioned
S3 bucket before enabling the nightly schedule. It does not shut down/start the app.
The controller is a private Lambda with one concurrent operation; invoking ON/OFF
requires AWS credentials. Mode lives in SSM. Logs retain 14 days, and a CloudWatch
Errors alarm records failures without sending external messages.

The controller fails closed if it cannot save/verify recovery state. Failed partial
operations preserve `restore.json`; retry the same action after checking `status`
and `/aws/lambda/agentd-production-power` logs. Nightly OFF can queue while a manual
startup is in progress. Deliveries older than 30 minutes, or predating a subsequent
manual start, are rejected. Retries after function errors are disabled. These rules
prevent an old shutdown from stopping a later manual start. No path schedules automatic ON.
In keep-on mode, the daily invocation returns without any compute changes.

`up.ps1` and `down.ps1` now call the production power controller, with no Terraform
apply or dev/staging fallback. The original remote state bucket is absent. Do not
run Terraform apply until state is recovered or imported, since it could conflict
with preserved resources.

Installer permissions are broader than runtime permissions: it creates the private
bucket, two scoped IAM roles, Lambda, SSM mode, nightly schedule, and error alarm.
Runtime permissions target this cluster, its host groups, load balancer names,
existing worker functions, and captured DNS names; it has no permission to delete
databases, stored files, secrets, keys, target groups, or certificates.

AWS scheduling reference: [Schedule types and timezones](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html).
