# stack/ — the whole agentd cloud environment, flat

One Terraform root, one (local) state. No modules: every resource is a plain block and
every reference is a direct resource reference. Concerns are split by **file**, not by
module:

| File           | What's in it                                                        |
| -------------- | ------------------------------------------------------------------- |
| `providers.tf` | Terraform + provider requirements                                    |
| `variables.tf` | Inputs **and the `services` map** — the data that says what runs     |
| `network.tf`   | VPC, 2 public subnets, IGW, routes                                   |
| `security.tf`  | The 4 security groups (alb / service / rds / efs) + their rules      |
| `iam.tf`       | ECS execution role + task role                                       |
| `ecr.tf`       | One image repository per service                                     |
| `cluster.tf`   | ECS cluster, log group, private DNS namespace (`agentd.local`)       |
| `data.tf`      | Secrets Manager app secret, EFS + access point                       |
| `alb.tf`       | The load balancer, one target group + listener per service           |
| `services.tf`  | Task definition + service discovery + Fargate service, per service   |
| `outputs.tf`   | URLs and push targets                                                |
| `moved.tf`     | modules → flat migration map (delete after the move is applied once) |

**Adding a container** = adding one entry to the `services` map in `variables.tf`
(port, health path, env, secret keys, efs). It gets its ECR repo, ALB wiring,
firewall holes, DNS name, and Fargate service automatically.

Sibling roots with separate lifecycles (rarely applied, admin credentials):
`../bootstrap` (state bucket), `../github-oidc` (CI deploy role).

## Day-to-day scripts

- `./down.ps1` / `./up.ps1` — pause/resume the compute bill (tasks → 0 / 1)
- `./push-images.ps1` — build + push the 4 images, roll the services
- `./set-keys.ps1` — push real provider keys from `v2/.env` into the app secret

## One-time migration from the old modules layout

The old roots (`../environments/dev`, `../modules/*`) are replaced by this directory.
Dev's state is local, so:

```powershell
cd v2/deploy/aws
Copy-Item environments/dev/terraform.tfstate stack/
cd stack
terraform init      # providers already validated; picks up the copied state
terraform plan
```

The plan must show **0 to add, 0 to destroy** — every deployed resource just *moves*
to its new address (via `moved.tf`). A handful of security-group rules may show an
in-place update because their `description` text was normalized; that is the only
acceptable change. Then:

```powershell
terraform apply        # records the moves in state
```

After a clean apply, delete `../environments/` and `../modules/` (and `moved.tf`
whenever you like).
