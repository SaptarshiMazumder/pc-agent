# Terraform × AWS — Zero → Expert Curriculum

**The goal:** you become flawless at Terraform on AWS. **The vehicle:** this repo's real
infrastructure (`v2/deploy/aws/`) — every concept is taught through the actual files that
deploy agentd, not toy examples.

**How we work (the contract):**
- **Concept FIRST, code LAST — always.** Code is never dumped and then explained. Every
  lesson runs the same five-step flow:
  1. **IDEA** — what the thing *is*, the problem it exists to solve, why it exists at all.
     Plain language, diagrams, analogies. Zero code on the table.
  2. **VOCABULARY** — the names AWS/Terraform use for it, why those names, what each knob
     (argument) means and where it's officially documented (registry/AWS docs links).
  3. **PREDICT** — before seeing any repo code, we sketch together what the code *should*
     look like: "given all that, what would you expect this resource block to need?" You
     should already hold a mental image of the file before it's opened.
  4. **REVEAL** — only now open the repo file, as the *living example*. Reading it should
     feel like recognition ("yes, that's what we said it would be"), and every line that
     *differs* from the prediction becomes its own why-question.
  5. **LAB + MASTERY CHECK** — a command card is provided (you run it whenever you choose;
     labs are optional/deferrable, never blocking). The mastery-check questions are posed
     AND answered by the instructor at the end of each lesson — model answers as the
     lesson's closing summary — before moving to the next lesson.
- **You drive the terminal.** I never run `terraform`/`aws` commands. Each lab ends in a
  *command card*: exact commands, what success looks like, what to paste back if it doesn't.
- **One lesson at a time, slow + atomic.**
- **Progress ledger:** mark lessons `[x]` as we complete them. This file is the syllabus;
  the depth happens in chat. File links in lessons below are the step-4 REVEAL material —
  not the starting point.

**The map of what you already have** (we study it, then extend it):

```
bootstrap/            local-state config that creates the S3 state bucket   → Unit 2
modules/network       VPC, subnets, IGW, routes          (count, data)      → Unit 5.1
modules/security      4 security groups                  (SG references)    → Unit 5.2
modules/iam           execution + task roles             (policy docs)      → Unit 5.3
modules/ecr           4 registries in one loop           (for_each)         → Unit 5.4
modules/cluster       ECS cluster, logs, Cloud Map                          → Unit 5.5
modules/data          Secrets Mgr, EFS                   (lifecycle, random)→ Unit 5.6
modules/alb           LB, target groups, listeners       (for_each on map)  → Unit 5.7
modules/service       task def, discovery, ECS service   (dynamic blocks)   → Unit 5.8
environments/dev      the RECIPE composing all modules   (moved block)      → Unit 4
environments/staging  same recipe, one word changed                         → Unit 4
```

---

## Unit 0 — Orientation: what Terraform actually is

- [x] **0.1 The mental model.** Infrastructure-as-Code; *declarative* ("this is what should
  exist") vs *imperative* ("run these steps"); Terraform as a **reconciliation engine**:
  `desired (your .tf) − actual (state/cloud) = plan`. Where Terraform sits vs
  CloudFormation, CDK, Pulumi, Ansible — and why HCL. No lab; pure concept + Q&A.
- [x] **0.2 Tour of this repo's architecture.** Read [README.md](README.md) +
  [architecture.puml](architecture.puml) together: the 4 containers, VPC, ALB, EFS,
  Secrets — so every later lesson has a place on the map. Mastery: you redraw the
  architecture from memory and explain why each AWS piece exists.

## Unit 1 — The core workflow (the loop you'll live in forever)

- [x] **1.1 Anatomy of a config.** The `terraform {}` block, `required_version`,
  `required_providers`, the `~> 5.0` pessimistic pin, `provider "aws" {}`.
  Files: [environments/dev/main.tf:6-23](environments/dev/main.tf#L6-L23).
  Why providers are *plugins* (separate binaries — see
  `environments/dev/.terraform/providers/`).
- [x] **1.2 `terraform init`.** What it actually does: downloads providers, resolves module
  `source`s, writes `.terraform/` + [.terraform.lock.hcl](environments/dev/.terraform.lock.hcl)
  (the lock file = *reproducibility*; committed, like package-lock.json).
  Lab: `init` in `environments/staging` and inspect what appeared.
- [x] **1.3 `terraform plan` — reading plans is THE core expert skill.** The refresh step,
  the diff symbols (`+` create, `-` destroy, `~` update in-place, `-/+` destroy-and-recreate,
  `<=` data read), `(known after apply)`, `-out=tfplan`. Lab: run `plan` on dev (should be
  ~clean), then make a harmless edit (add a tag), plan again, read the diff aloud, revert.
- [x] **1.4 `terraform apply` + the dependency graph.** How Terraform orders work: implicit
  dependencies from references (`module.network.vpc_id` ⇒ network before security),
  parallelism (default 10), `depends_on` for the rare invisible dependency.
  Lab: `terraform graph` on dev; trace why the ALB waits for the VPC.
- [x] **1.5 Hygiene commands.** `fmt` (canonical style), `validate` (schema check without
  cloud), `console` (interactive expression REPL — we'll use it constantly in Unit 3),
  `output`, `providers`. Lab: run all four on dev, then evaluate
  `module.ecr.repository_urls` in `console`.
- [x] **1.6 `terraform destroy` and partial ops.** `-target` (and why experts avoid it),
  `-replace=<addr>` (modern taint), destroy ordering (reverse graph). Concept +
  [up.ps1](environments/dev/up.ps1)/[down.ps1](environments/dev/down.ps1) walkthrough —
  what the cost scripts do instead of destroy.

## Unit 2 — State: the soul of Terraform

- [x] **2.1 What `terraform.tfstate` is.** The mapping *resource address → real AWS ID*;
  why deleting it orphans (not deletes) infra. Lab: `terraform state list`,
  `state show module.network.aws_vpc.main` on dev; open
  [terraform.tfstate](environments/dev/terraform.tfstate) read-only and find the VPC id.
- [x] **2.2 Drift.** What happens when someone clicks in the AWS console;
  `plan -refresh-only` vs plan's auto-refresh. Lab: change a tag on the VPC in the AWS
  console by hand, watch Terraform detect + heal it, understand which side "wins".
- [x] **2.3 Refactoring without destroying: `moved`.** This repo has a live specimen —
  [environments/dev/main.tf:229-232](environments/dev/main.tf#L229-L232) re-homed the
  pre-module gateway ECR repo into `module.ecr["gateway"]`. Also `state mv` (imperative
  cousin) and when each applies. Mastery: explain what would have happened *without* that
  block (hint: your pushed images).
- [x] **2.4 Adopting existing infra: `import`.** `import` blocks (declarative, 1.5+) vs the
  CLI command. Lab: create a tiny resource by hand in the console, import it, then let
  Terraform destroy it.
- [x] **2.5 Remote state — the chicken-and-egg.** (taught; migration lab pending — command card issued) Why local state can't be shared or locked;
  the [bootstrap/main.tf](bootstrap/main.tf) trick (a local-state config that creates the
  versioned+encrypted+private S3 bucket); the `backend "s3"` block; state locking;
  `init -migrate-state`. Lab: **for real** — apply bootstrap, add backend blocks to
  dev/staging, migrate dev's state to S3, verify versioning. (This is a planned repo TODO —
  we do it as the lesson.)

## Unit 3 — The HCL language, completely

- [x] **3.1 Resources & references.** Address anatomy (`aws_subnet.public[0]`,
  `module.ecr.aws_ecr_repository.this["gateway"]`); attributes vs arguments;
  interpolation `"${...}"` vs bare expressions.
- [x] **3.2 Data sources — read, don't create.** The three in this repo:
  [aws_availability_zones](modules/network/main.tf#L21-L23) (don't hardcode AZs),
  [aws_caller_identity](bootstrap/main.tf#L27) (account id for globally-unique names),
  [aws_iam_policy_document](modules/iam/main.tf#L19-L27) (HCL → policy JSON).
- [x] **3.3 Variables vs locals.** `variable` = module *input* (types, defaults,
  `validation`, `sensitive`); `local` = module-internal *computed value*
  (the `name_prefix`/`common_tags` pattern in every module); `.tfvars` files,
  `TF_VAR_` env vars, and the full precedence order. Lab: add a `validation` block to
  a module variable and trigger it on purpose.
- [x] **3.4 Outputs.** Module outputs as the *public API* (every `outputs.tf` here);
  root outputs as the human/CI interface (`app_url`); `sensitive = true`.
- [x] **3.5 `count` vs `for_each` — the classic exam question.** Repo has both:
  [subnets use count](modules/network/main.tf#L25-L32) (ordered, index-keyed) vs
  [ECR uses for_each](modules/ecr/main.tf#L13-L14) (name-keyed). THE pitfall: removing
  item 0 of a `count` list renumbers-and-destroys everything after it; `for_each` keys
  are stable. `toset()`, `each.key`/`each.value`, for_each over maps
  ([alb target groups](modules/alb/main.tf#L30-L48)). Mastery: predict a plan's blast
  radius for removing one subnet vs one ECR repo.
- [x] **3.6 Expressions & functions.** `for` expressions
  ([env/secrets maps → lists](modules/service/main.tf#L52-L53)), conditionals
  (`local.mount_efs ? [...] : []`), `merge()` (tag layering), `jsonencode()`
  (container defs, IAM policies), splats, `length()`, string templates. Lab: predict
  outputs in `terraform console`, then verify.
- [ ] **3.7 `dynamic` blocks — conditional structure.** Both specimens in
  [modules/service/main.tf](modules/service/main.tf): the EFS `volume` (only if an
  access point was passed) and the ALB `load_balancer` (only if `exposed`). The
  `for_each = condition ? [1] : []` idiom. Mastery: explain why a plain `count` on the
  resource couldn't express this.
- [ ] **3.8 `lifecycle` meta-arguments.** The repo's crown jewel:
  [ignore_changes on the secret version](modules/data/main.tf#L52-L54) — Terraform
  creates placeholder secrets, you set real values out-of-band, Terraform never reverts
  them (keys stay out of git AND state churn). Also `prevent_destroy`,
  `create_before_destroy`, `replace_triggered_by`.

## Unit 4 — Modules & architecture (why this repo is shaped like this)

- [ ] **4.1 Module anatomy.** The `variables.tf` / `main.tf` / `outputs.tf` convention;
  `source = "../../modules/x"`; how a module call is really just "inputs in, outputs out".
- [ ] **4.2 Composition — environments as recipes.** How
  [dev/main.tf](environments/dev/main.tf) wires `module.network.vpc_id` →
  `module.security`, `module.security.efs_sg_id` → `module.data`, etc.; the
  `svc_shared` local pattern; why [staging](environments/staging/main.tf) is the same
  recipe with one word changed. Mastery: whiteboard the full output→input wiring graph
  from memory.
- [ ] **4.3 Design principles.** One concern per module; no copy-pasted resources between
  environments; nothing hardcoded (project/environment flow in as variables — mirrors
  your #1 rule); directory-per-environment vs `terraform workspace` (and why this repo
  chose directories).
- [ ] **4.4 Module ecosystems.** Registry modules (`terraform-aws-modules/vpc`), version
  pinning for remote modules, when to use vs write your own (this repo: own —
  learning + control).

## Unit 5 — AWS itself, one module at a time (deep dives)

Each lesson: the AWS service from first principles (what problem it solves, its
vocabulary, its moving parts — no code yet) → we predict what the Terraform for it must
look like → only then the module file, read as confirmation → a lab poking the real
resources (console + CLI) → mastery check.

- [ ] **5.1 [network](modules/network/main.tf)** — VPC, CIDR math (`10.0.0.0/16` →
  `/24` per subnet), Availability Zones, public vs private subnets, Internet Gateway,
  route tables & associations, `map_public_ip_on_launch`, why dev skips the NAT
  gateway (~$40/mo).
- [ ] **5.2 [security](modules/security/main.tf)** — security groups as *stateful*
  firewalls; ingress vs egress; the expert move: **SG-references-SG** rules
  (`referenced_security_group_id`) instead of CIDRs — "from the ALB", not "from an IP";
  the 4-tier trust chain: internet → alb → service → rds/efs.
- [ ] **5.3 [iam](modules/iam/main.tf)** — identities vs policies; **trust policy**
  ("who may wear this role") vs **permission policy** ("what the wearer may do");
  managed vs inline; the ECS pair: *execution* role (AWS starting your task: pull
  image, write logs, read secrets) vs *task* role (your code at runtime: EFS); least
  privilege (`agentd/dev/*` secrets only — never prod's).
- [ ] **5.4 [ecr](modules/ecr/main.tf)** — registries, tag mutability (dev MUTABLE
  `latest` vs prod IMMUTABLE), scan-on-push, `force_delete`, how the docker push
  actually authenticates ([push-images.ps1](environments/dev/push-images.ps1)).
- [ ] **5.5 [cluster](modules/cluster/main.tf)** — what ECS *is* (control plane), Fargate
  vs EC2 launch types, CloudWatch log groups + retention (cost), Cloud Map private DNS
  (`gateway.agentd.local` — the cloud version of docker-compose service names).
- [ ] **5.6 [data](modules/data/main.tf)** — Secrets Manager (secret vs secret-*version*,
  recovery windows, the placeholder + [set-keys.ps1](environments/dev/set-keys.ps1)
  out-of-band pattern); the `random` provider (second provider! generated master key);
  EFS: file system vs *mount targets* (one per AZ) vs *access points* (subdir + fixed
  POSIX identity); why RDS was deliberately deferred (app still speaks SQLite).
- [ ] **5.7 [alb](modules/alb/main.tf)** — L7 load balancing; listener → target group →
  targets; `target_type = "ip"` (because Fargate/awsvpc); health checks (path, matcher,
  thresholds); the WebSocket `idle_timeout` tweak; port-based vs path-based routing
  (README's Phase 5 evolves this).
- [ ] **5.8 [service](modules/service/main.tf)** — the capstone module. Task definition
  (family, cpu/memory pairs, `jsonencode`d container definitions), env vars vs
  `valueFrom` secrets (the `arn:...:key::` syntax), awslogs driver, EFS volume +
  transit encryption + IAM auth, service discovery registration, the ECS service
  reconciler (`desired_count`), `assign_public_ip` (the no-NAT consequence), how ONE
  module runs 4 different containers. Mastery: you write the recipe block for a
  hypothetical 5th container unaided.

## Unit 6 — Operating like an expert (day-2 and beyond)

- [ ] **6.1 Safe change discipline.** Read-the-plan ritual; what forces *replacement* vs
  update-in-place (and how to spot it before it bites); `-replace` for sick resources;
  blast-radius thinking.
- [ ] **6.2 Debugging.** `TF_LOG=DEBUG`, provider vs core errors, dependency cycles,
  eventual-consistency retries, "inconsistent final plan" — the greatest hits and how
  to read them.
- [ ] **6.3 Secrets & state hygiene.** What leaks into state (yes: `random_password`,
  RDS master passwords), why state is treated as secret material (encrypted bucket in
  bootstrap), `sensitive` marking, ephemeral resources (TF 1.10+).
- [ ] **6.4 Backend migration in anger** (executes with 2.5 if not already done) +
  state recovery drills: restore a previous state version from S3.
- [ ] **6.5 CI/CD for Terraform.** `fmt -check` + `validate` + `plan` on PR, plan file as
  artifact, apply on merge with approvals — wired into your existing GitHub Actions
  plan (`v2/planning/platform/ci-cd-plan.md` Stage 4). OIDC federation instead of
  long-lived AWS keys in CI.
- [ ] **6.6 Cost engineering.** What bills while idle (ALB, RDS) vs at rest (ECR/EFS/S3);
  the up/down scripts; tagging for cost allocation (`common_tags` pays off here).
- [ ] **6.7 Production deltas.** What prod adds over dev and *why*: private subnets + NAT,
  RDS multi-AZ, immutable image tags, CloudFront + ACM (us-east-1 quirk) + Route 53,
  deletion protection, `prevent_destroy`.
- [ ] **6.8 Quality gates.** `tflint`, `checkov`/`trivy` policy scanning,
  native `terraform test` (1.6+), pre-commit hooks.

## Capstone — prove it

- [ ] **C.1** Build `environments/prod` solo: private subnets + NAT, immutable ECR,
  remote state, `prevent_destroy` on data stores. I only review your plan output.
- [ ] **C.2** Wire the RDS module back in (module design + `moved`/migration reasoning).
- [ ] **C.3** Break-glass drill: I describe a broken state scenario (drift + a failed
  apply + a lost lock), you recover it narrating every command before running it.

---

*Ledger: `[x]` = mastered (mastery check passed, not just "read it"). Started 2026-07-23.*
