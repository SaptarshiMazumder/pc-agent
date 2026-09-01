# agentd on AWS — Deployment Plan (Fargate)

**This is the ordered runbook** we execute to get agentd's full architecture live on AWS.
The *why* (architecture, planes, isolation) lives in
[`v2/planning/platform/platform-plan.md`](../../planning/platform/platform-plan.md) and the
diagrams (`platform/diagrams/aws-deployment.puml`, `isolation-model.puml`). This file is the
*how* and *in what order*.

**Approach:** infrastructure is split into focused, single-purpose modules (`modules/network`,
`security`, `iam`, `cluster`, `ecr`). Each environment (`environments/dev`, `staging`, `prod`) is
a short RECIPE that composes those modules with its own name and wires their outputs together —
no copy-pasting resources between environments. Build + go live in `dev` first. Local state for
now; remote state (S3) deferred until CI.

---

## Target picture (what "live" means)

Your app = **4 containers** + a database + user files, behind one public URL:

```
                    Internet
                       │
                 ┌─────▼─────┐   public URL (ALB DNS, later CloudFront+domain)
                 │    ALB    │
                 └──┬─────┬──┘
          /  (web) │     │ /ws, /api (daemon)
              ┌────▼─┐ ┌─▼─────┐
              │ web  │ │ daemon│──────┐ (calls sibling services by name via Cloud Map)
              └──────┘ └───┬───┘      │
                           │          ▼
                    ┌──────▼──┐   ┌────────┐
                    │ accounts│   │model-proxy│ (LiteLLM → provider keys from Secrets Mgr)
                    └────┬────┘   └────────┘
                         │
   EFS (daemon /data, user files)   RDS Postgres (accounts DB)   Secrets Mgr (keys)
```

All of it runs inside one **VPC**, on **ECS Fargate**, images pulled from **ECR**.

---

## Build order (the plan)

Legend: ✅ done · 🔨 in progress · ⬜ todo. Each step notes **why the app needs it** and **what
it depends on**.

### Phase 1 — Foundation (plumbing; no visible app yet)
| # | Step | Why the app needs it | Depends on |
|---|---|---|---|
| 1.1 | 🔨 **VPC + 2 public subnets + IGW + routes** (`network.tf`) | containers need a network to run in | — |
| 1.2 | ⬜ **Security groups** (ALB, service, RDS, EFS) | firewall: who may talk to whom | 1.1 |
| 1.3 | ⬜ **IAM roles** (task-execution + task role) | so tasks can pull images, read secrets, mount EFS | — |
| 1.4 | ⬜ **ECR — 4 repos** (model-proxy, accounts, daemon, web) | a home for each image | — (extend `modules/ecr`) |
| 1.5 | ⬜ **ECS cluster** (empty) | the logical place Fargate tasks run | — |
| 1.6 | ⬜ **CloudWatch log group** | somewhere for container logs to go | — |
| 1.7 | ⬜ **Cloud Map private DNS namespace** (`agentd.local`) | so daemon can reach `model-proxy`/`accounts` by name (like docker-compose does) | 1.1 |

### Phase 2 — Data & secrets (what the containers need to exist first)
| # | Step | Why | Depends on |
|---|---|---|---|
| 2.1 | ⬜ **Secrets Manager** (provider keys, LiteLLM master key, DB password) | keys never baked into images | — |
| 2.2 | ⬜ **RDS Postgres** (accounts DB) | durable accounts/metering store | 1.1, 1.2 |
| 2.3 | ⬜ **EFS** (filesystem + mount targets + access point for `/data`) | daemon's per-user files, survives task restarts | 1.1, 1.2 |

### Phase 3 — Expose (the public front door — built before services so they can attach)
| # | Step | Why | Depends on |
|---|---|---|---|
| 3.1 | ⬜ **ALB** (public, in the 2 public subnets) | the public entry point | 1.1, 1.2 |
| 3.2 | ⬜ **Target groups** (web:80, daemon:8787) | where the ALB sends traffic | 3.1 |
| 3.3 | ⬜ **Listener + routing rules** (`/`→web, ws/api→daemon) | maps URL paths to containers | 3.2 |

### Phase 4 — Run the containers (the app appears)
| # | Step | Why | Depends on |
|---|---|---|---|
| 4.1 | ⬜ **Build + push 4 images to ECR** | Fargate has something to run | 1.4 |
| 4.2 | ⬜ **Task definitions** (4: image, cpu/mem, ports, env, secrets, EFS mount, logs) | the "how to run" spec per container | 1.3–2.3, 4.1 |
| 4.3 | ⬜ **ECS services** (4; attach to target groups + Cloud Map) | keeps N copies running + wired to ALB | 3.2, 4.2, 1.5, 1.7 |
| ➡ | **Output: ALB DNS name = your live URL** | 🎉 | 4.3 |

### Phase 5 — Later (hardening / not needed for first live URL)
- **CloudFront + ACM cert (us-east-1) + Route 53 domain** — TLS + a real URL.
- **`staging` / `prod` environments** — prod adds private subnets + NAT, RDS multi-AZ, IMMUTABLE ECR.
- **Remote state (S3 backend)** + **CI (GitHub Actions)** + **GitHub Environments** (approval gates).
- **Cost up/down scripts** — scale Fargate services to 0 + stop RDS overnight; keep EFS/S3/ECR.

---

## File layout this produces

```
v2/infra/
├── README.md              ← this plan
├── bootstrap/             ← S3 state bucket (deferred)
├── modules/               ← reusable building blocks (each = ONE concern)
│   ├── network/           ← VPC + subnets + routes                 (Phase 1) ✅
│   ├── security/          ← security groups (firewalls)            (Phase 1) ✅
│   ├── iam/               ← task roles                             (Phase 1) ✅
│   ├── cluster/           ← ECS cluster + logs + service-discovery (Phase 1) ✅
│   ├── ecr/               ← the 4 container registries (for_each)  (Phase 1) ✅
│   ├── data/              ← RDS + EFS + Secrets                     (Phase 2) [coming]
│   ├── alb/               ← load balancer + routing                (Phase 3) [coming]
│   └── service/           ← task def + service (one per container) (Phase 4) [coming]
└── environments/          ← RECIPES that compose the modules (~40 lines each)
    ├── dev/main.tf        ← module network/security/iam/ecr/cluster { environment = "dev" }
    └── staging/main.tf    ← same recipe, environment = "staging"
```

## Cost & teardown
- **Always-on while up:** ALB (~$18/mo), RDS (~$13/mo). Fargate = per-second (scale to 0 = $0).
- **Cheap at rest:** ECR, EFS, S3, Secrets — leave them.
- **We deliberately skip the NAT Gateway** in dev (public subnets) → saves ~$40/mo.
- Teardown for the day (Phase 5 scripts): scale services to 0 + stop RDS; morning: reverse.

## Where we are
Phase **1.1** — the VPC is written (`network.tf`) and ready to apply.
