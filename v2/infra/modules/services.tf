# The containers. ONE set of for_each resources over the services map — per service:
#   • a task definition (the blueprint: image, cpu/mem, env, secrets, logs, optional EFS)
#   • a service-discovery entry (so siblings reach it at <name>.agentd.local)
#   • an ECS service (keeps N copies alive, registered with its ALB target group)
# Adding a container to the platform = adding an entry to local.services. That's it.

locals {
  # Env a service needs that CANNOT be written in var.services: a variable's default cannot
  # reference a resource attribute, and these values are only known once the resource exists.
  #
  # Kept out of local.services on purpose, even though merging it there would read better. That
  # map drives `for_each` on five resources, and a for_each whose contents depend on a
  # not-yet-created resource fails the plan outright ("Invalid for_each argument … cannot be
  # determined until apply") — on a FRESH apply, where the bucket name derives from an unborn
  # random_id. Merging here instead keeps the computed value inside a resource ARGUMENT, which is
  # allowed to be unknown at plan time. Any service can take computed env by adding a key.
  computed_env = {
    # THE MARKETPLACE, for the hosted daemon. Browsers talk to this daemon, and a daemon can only
    # list a store it has been told the url of — nothing discovers it. Without these two the web
    # client's Store renders "no registry configured" while the desktop's works, because the
    # desktop's installer bakes the same pair into its distribution.toml.
    #
    # Both, never just the url: AGENTD_REGISTRY alone leaves publisher_key empty, which turns off
    # signature verification and leaves only the checksum — see the comment on AGENTD_PUBLISHER_KEY
    # in agent_runtime/config.py for why that is a downgrade and not a shortcut.
    daemon = {
      AGENTD_REGISTRY      = local.registry_index_url
      AGENTD_PUBLISHER_KEY = var.registry_publisher_key
      # WHO MAY EDIT THIS DEPLOYMENT'S DEFAULTS — the same list the accounts and publish services
      # read, now a third reader. The daemon needs it because the config every tenant inherits is
      # ITS file (/data/config.json on EFS): `config.set {target:"master"}` is admitted only for
      # an identity named here, and an empty list means nobody rather than everybody.
      #
      # It is deliberately the break-glass list and NOT the promotable roster in the accounts
      # database: that roster governs accounts, credits and creators, while this governs the
      # machine those accounts run on.
      AGENTD_ADMIN_IDENTITIES = join(",", var.publish_admin_identities)
      # What to tell a BROWSER when it asks where to sign in. AGENTD_ACCOUNTS_URL (above) is
      # internal service DNS — correct for this container's own calls, unresolvable from a
      # visitor's machine, and advertising it gives an agent app a login form that can never
      # submit. Same expression the publish service uses, so both name one address.
      AGENTD_PUBLIC_ACCOUNTS_URL = local.publish_product_accounts_url
      # WHERE AGENT WINDOW BUILDS RUN (builder.tf). Set => the agent-authoring plugin sends
      # builds out over the scratch bucket instead of running npm in this container — which is
      # what OOM-killed the 512 MB task. Empty (builder not brought up) => builds stay local,
      # which on a small task means create/edit of windowed agents will fail; the empty value
      # keeps that failure honest rather than hiding the dependency.
      AGENTD_BUILDER_URL            = local.builder_public_url
      AGENTD_BUILDER_SCRATCH_BUCKET = aws_s3_bucket.builder_scratch.bucket
      AGENTD_BUILDER_INTERNAL_KEY   = random_password.builder_internal_key.result
      # VANITY HOSTNAMES, the daemon's half of the same map that drives the ALB rules
      # (alb.tf agent_host) — one variable, two consumers, so DNS pointing at a daemon that
      # does not know what to serve there cannot happen. "{}" when unset, which the parser
      # reads as the dormant default.
      AGENTD_APP_HOSTS = jsonencode(var.agent_hostnames)
      # THE WILDCARD's daemon half: with root_domain set, <bundle-id>.<root_domain> serves
      # that agent with no per-agent configuration anywhere — publishing mints the URL, the
      # bundle_owners table is the namespace, and reserved labels never derive
      # (agent_runtime/domain/reserved_hosts.py). Empty = dormant.
      AGENTD_APP_HOST_SUFFIX = var.root_domain
    }

    # The web image needs to know which hostname is the ADMIN CONSOLE's: nginx tells the app and
    # the console apart by server_name (web/nginx.conf is a template, substituted at container
    # start), so the image itself stays domain-agnostic. Empty = the console lives at /admin on
    # the app's own host, exactly the pre-domain behaviour.
    web = {
      ADMIN_HOSTNAME = var.admin_hostname
    }

    # The proxy VERIFIES what accounts MINTS, so both read the SAME computed issuer below. A
    # mismatch between them rejects every token with a confusing "issued by another deployment",
    # which is why neither is a configurable input.
    "model-proxy" = {
      # Computed rather than a static entry in var.services because it reads a variable, and a
      # variable's default cannot reference another variable. See `require_credits` for what the
      # switch buys and the one case it cannot cover (anonymous public-app visitors).
      MODEL_PROXY_REQUIRE_CREDITS = var.require_credits ? "1" : "0"
      AGENTD_AUTH_ISSUER          = local.publish_product_accounts_url
      # Fetched over service-discovery DNS: a server-to-server call inside the VPC, not something
      # a browser does, so it must NOT use the public host.
      AGENTD_AUTH_JWKS_URI = "http://accounts.${var.project}.local:${local.services["accounts"].port}/auth/jwks.json"
    }

    # THE TOKEN ISSUER FOR THIS ENVIRONMENT. Computed rather than configured because it must be
    # this stack's own public address and nothing else: it is stamped into every access token as
    # `iss` and checked by the daemon and the model proxy, so a dev token presented to production
    # is refused by name instead of silently resolving to a second account with its own credits.
    #
    # That silent-second-account failure is exactly what the drifted per-flavor accounts URLs
    # produced, and deriving the issuer from the same expression as `accounts_url` is what stops
    # the two from ever disagreeing again.
    #
    # EMPTY WHILE HIBERNATING (no public host) is a supported state, not a broken one: the service
    # then serves the legacy `sess_` path and reports /auth/* as not-configured.
    accounts = {
      AGENTD_AUTH_ISSUER = local.publish_product_accounts_url
      # What the discovery document (/.well-known/agentd-platform) hands a browser. These are
      # PUBLIC addresses; the internal *.agentd.local names other services use would produce a
      # document that works inside the VPC and fails for every real user.
      AGENTD_PUBLIC_ACCOUNTS_URL    = local.publish_product_accounts_url
      # THE DAEMON'S OWN ADDRESS, port included. This read `replace(app_origin, "http", "ws")`,
      # and app_origin is the WEB client's origin — so the document handed every browser
      # ws://<host> with no port, i.e. nginx on :80, which serves static files and answers no
      # WebSocket upgrade. Same shape as publish_web_host and the model-proxy line below: one
      # host, each service named with ITS port. wss when TLS is on, because a page loaded over
      # https may not open a ws:// socket.
      AGENTD_PUBLIC_WS_URL = local.public_host == "" ? "" : "${local.tls_enabled ? "wss" : "ws"}://${local.public_host}:${local.services["daemon"].port}"
      AGENTD_PUBLIC_MODEL_PROXY_URL = local.public_host == "" ? "" : "${local.url_scheme}://${local.public_host}:${local.services["model-proxy"].port}"

      # ── the admin control plane (accounts/admin_api.py) ──
      #
      # WHO MAY ADMINISTER, from the SAME variable the publish service reads. One list, two
      # services, no way for them to disagree about who is an admin. This is the break-glass tier
      # — permanent while deployed and not demotable from the dashboard — and the editable roster
      # lives in the accounts database beside it.
      AGENTD_ADMIN_IDENTITIES = join(",", var.publish_admin_identities)
      # WHERE EVERYTHING IS. All derived from the resources themselves, so no address, table or
      # ARN is typed twice, and an environment cannot end up pointed at another one's.
      AGENTD_APP_SECRET_ID   = aws_secretsmanager_secret.app.arn
      AGENTD_CREATORS_TABLE  = aws_dynamodb_table.creators.name
      AGENTD_PUBLISH_KMS_KEY = aws_kms_alias.publish.name
      AGENTD_ECS_CLUSTER     = aws_ecs_cluster.main.name
      AGENTD_REGISTRY        = local.registry_index_url
      AGENTD_PUBLISH_URL     = local.publish_public_url
      # WHO READS WHICH SECRET, INVERTED FROM THE SERVICES MAP ITSELF rather than written out.
      # ECS injects secrets at container start, so setting a provider key does nothing until the
      # containers reading it restart — the dashboard rolls them, and this is how it knows which.
      # Because it is derived, adding a secret to any service's `secret_keys` makes the rollout
      # correct for free; a hand-maintained copy would drift silently and the symptom would be a
      # new key that appears to have been set and has not taken effect anywhere.
      # The values are the ECS SERVICE names (prefixed), because that is what UpdateService takes
      # — not the bare map keys, which would 404 against the cluster.
      AGENTD_KEY_CONSUMERS = jsonencode({
        for secret_key in distinct(flatten([
          for svc in values(local.services) : values(svc.secret_keys)
        ])) : secret_key => sort([
          for name, svc in local.services :
          "${local.name_prefix}-${name}" if contains(values(svc.secret_keys), secret_key)
        ])
      })
    }
  }
}

resource "aws_ecs_task_definition" "svc" {
  for_each = local.services

  family                   = "${local.name_prefix}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution.arn
  # The control plane wears a role the other containers do not — see iam.tf for why the two are
  # separate rather than one role with everything on it.
  task_role_arn = each.value.admin_plane ? aws_iam_role.task_admin.arn : aws_iam_role.task.arn

  # Only attach an EFS volume when the service asks for one (efs = true).
  dynamic "volume" {
    for_each = each.value.efs ? [1] : []
    content {
      name = "data"
      efs_volume_configuration {
        file_system_id     = aws_efs_file_system.main.id
        transit_encryption = "ENABLED"
        authorization_config {
          access_point_id = aws_efs_access_point.data.id
          iam             = "ENABLED"
        }
      }
    }
  }

  container_definitions = jsonencode([{
    name         = each.key
    image        = "${aws_ecr_repository.this[each.key].repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = each.value.port }]
    environment  = [for k, v in merge(each.value.env, lookup(local.computed_env, each.key, {})) : { name = k, value = v }]
    # secret_keys: container env var -> JSON key inside the app secret.
    secrets = [for k, v in each.value.secret_keys : {
      name      = k
      valueFrom = "${aws_secretsmanager_secret.app.arn}:${v}::"
    }]
    mountPoints = each.value.efs ? [{ sourceVolume = "data", containerPath = "/data", readOnly = false }] : []
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.agentd.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = each.key
      }
    }
  }])

  tags = merge(local.common_tags, { Component = each.key })
}

# Service discovery: makes each service reachable at <name>.agentd.local.
resource "aws_service_discovery_service" "svc" {
  for_each = local.services

  name = each.key

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.common_tags, { Component = each.key })
}

# The service: keeps desired_count copies alive, wired to the network + the ALB.
resource "aws_ecs_service" "svc" {
  # Removed while HIBERNATING, not merely scaled to zero. The service's load-balancer attachment
  # would otherwise have to be edited in place when the ALB comes and goes, and the provider may
  # treat that as a replacement — which an ECS service cannot survive under create_before_destroy,
  # because two services in one cluster may not share a name. Deleting and recreating a stateless
  # service is the boring option, and boring is what a cost switch should be.
  for_each = local.alb_services

  name            = "${local.name_prefix}-${each.key}"
  cluster         = aws_ecs_cluster.main.arn
  task_definition = aws_ecs_task_definition.svc[each.key].arn
  launch_type     = "FARGATE"

  # Break-glass shell access (`aws ecs execute-command`) — see var.enable_execute_command.
  enable_execute_command = var.enable_execute_command

  # THE COST SWITCH (var.paused). Terraform owns this number so that turning the environment off
  # and on is `terraform apply` — the command you already run — and so the set of services being
  # paused is derived from local.services rather than typed into a script that has to be
  # remembered every time a service is added. A hand-maintained list had already drifted: it
  # still named four services after `ingest` became the fifth, so a "down" left one running.
  desired_count = local.paused ? 0 : each.value.desired_count

  # Without this, ECS retries a task that cannot start FOREVER: a container that crashes on
  # boot is relaunched indefinitely, the deployment never reaches a terminal state, and the
  # only thing that eventually gives up is the deploy workflow's waiter — after ten minutes,
  # with "Max attempts exceeded" and no hint of the cause. The breaker makes a broken image
  # fail as a DEPLOYMENT failure (rolloutState FAILED, with the reason) and puts the previous
  # working task definition back, which is what "the deploy failed" should mean.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # ROLLOUT ORDER. The ECS default (min 100% / max 200%) starts the replacement task BEFORE
  # draining the old one, which is right for a stateless service and wrong for one that owns a
  # single-writer datastore: for a few seconds two containers hold the same SQLite file on EFS,
  # and the newcomer dies on "database is locked" before it can finish its startup migration.
  # 0/100 inverts it — drain first, then start — so there is never more than one writer.
  deployment_minimum_healthy_percent = each.value.single_writer ? 0 : 100
  deployment_maximum_percent         = each.value.single_writer ? 100 : 200

  # Boot grace: health-check failures in this window don't count against the task, so a slow
  # starter is not killed before it can serve. It does NOT rescue a crash loop — a container
  # that exits is still a failure — so it costs the breaker nothing.
  health_check_grace_period_seconds = each.value.health_check_grace

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = true # public subnets, no NAT → tasks need a public IP to reach ECR/internet
  }

  service_registries {
    registry_arn = aws_service_discovery_service.svc[each.key].arn
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.svc[each.key].arn
    container_name   = each.key
    container_port   = each.value.port
  }

  # `ignore_changes = [desired_count]` USED TO BE HERE, so that down.ps1/up.ps1 could scale tasks
  # outside Terraform. It is gone on purpose: two mechanisms owned the same number, so the state
  # file and reality disagreed by design, and the only way to know whether an environment was
  # paused was to ask AWS. Terraform owns it now (var.paused) — which is also what makes pausing
  # a plain `apply` instead of a separate script.
  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.common_tags, { Component = each.key })
}
