# modules/ — the shared environment module

Every resource an agentd environment consists of, flat: plain resource blocks, split
by **file**, with direct references — no inner modules. This directory is a **child
module, not applied directly**. Each folder under `../environments/` (dev, staging,
prod) is a root module — provider + one `module "stack"` call + outputs — with its
**own state file**. Run terraform there.

```
aws/
  bootstrap/      state bucket        (applied once, own state)
  github-oidc/    CI deploy role      (applied once, own state)
  modules/        shared module       (never applied directly)
  environments/
    dev/          = provider + module "stack" { environment = "dev" }  + state
    staging/      = same, "staging"   (not applied yet)
    prod/         = same, "prod"      (not applied yet; IMMUTABLE tags, no force-delete)
```

| File           | What's in it                                                        |
| -------------- | ------------------------------------------------------------------- |
| `providers.tf` | Terraform + provider version requirements                            |
| `variables.tf` | Inputs **and the `services` map** — the data that says what runs     |
| `network.tf`   | VPC, 2 public subnets, IGW, routes                                   |
| `security.tf`  | The 4 security groups (alb / service / rds / efs) + their rules      |
| `iam.tf`       | ECS execution role + task role                                       |
| `ecr.tf`       | One image repository per service                                     |
| `cluster.tf`   | ECS cluster, log group, private DNS namespace (`agentd.local`)       |
| `data.tf`      | Secrets Manager app secret, EFS + access point                       |
| `alb.tf`       | The load balancer, one target group + listener per service           |
| `services.tf`  | Task definition + service discovery + Fargate service, per service   |
| `outputs.tf`   | URLs and push targets (re-exported by each environment root)         |

**Adding a container** = adding one entry to the `services` map in `variables.tf`
(port, health path, env, secret keys, efs). It gets its ECR repo, ALB wiring,
firewall holes, DNS name, and Fargate service automatically — in every environment.

**Adding an environment** = copying one small folder under `../environments/` and
changing the `environment` string on the module call.

## Day-to-day scripts (in `../environments/dev/`)

- `./down.ps1` / `./up.ps1` — pause/resume the compute bill (tasks → 0 / 1)
- `./push-images.ps1` — build + push the 4 images, roll the services
- `./set-keys.ps1` — push real provider keys from `v2/.env` into the app secret

## One-time migration (dev only — old modules layout → this one)

Dev's state (`../environments/dev/terraform.tfstate`) still holds the old
`module.network.*` addresses; `../environments/dev/moved.tf` maps every resource to
its new `module.stack.*` address. From `../environments/dev/`:

```powershell
terraform init
terraform plan     # expect: 0 to add, 0 to destroy — only "moved" notes
                   # (up to 8 SG rules may show description-only in-place updates)
terraform apply    # records the moves in state
```

After that clean apply, `moved.tf` is spent — delete it whenever. Staging and prod
have no state yet; their first `apply` builds them from scratch.
