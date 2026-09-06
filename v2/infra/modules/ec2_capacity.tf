# EC2 capacity for ECS — the machines Fargate used to provide invisibly.
#
# WHAT THIS FILE IS. Fargate hands ECS a machine per task and hides it; on EC2 the machine is
# ours, so the resources below exist to produce one: an AMI, a launch template that stamps
# instances from it, an Auto Scaling Group that keeps them alive, and a capacity provider that
# lets ECS add and remove them as tasks demand. The task definition never references any of it
# (see planning/platform/diagrams/ecs-on-ec2-offline.puml) — a service opts in through
# `capacity_provider_strategy`, which is the ONLY link between a workload and this file.
#
# APPLYING THIS ALONE CHANGES NOTHING. Every resource is gated on `ec2_capacity_enabled`, and
# with it set the ASG still starts at zero instances because no service references the provider
# yet. That is deliberate: the machinery lands as its own reviewable step, and moving the first
# service is a separate, revertible change.
#
# TWO NUMBERS THIS FILE DOES NOT OWN:
#   * `desired_capacity` — ECS managed scaling owns it. Terraform setting it too would mean two
#     writers for one number, which is the failure the `ignore_changes` note in services.tf
#     already describes for ECS services. Hence the lifecycle block below.
#   * the instance count while PAUSED — `max_size` drops to zero so the cost switch keeps
#     working. Without that, `terraform apply -var paused=true` would stop every task and keep
#     paying for the boxes they used to run on, which is the opposite of what the switch is for.

locals {
  ec2_capacity = var.ec2_capacity_enabled ? 1 : 0

  # PAUSED MEANS NO MACHINES, not merely no tasks. See the note above.
  ec2_max_size = local.paused ? 0 : var.ec2_max_instances
}

# ── the AMI ──────────────────────────────────────────────────────────────────
#
# READ FROM SSM RATHER THAN PINNED, because AWS publishes security updates to this parameter and
# a pinned id silently ages: the fleet would keep booting last year's kernel with no signal that
# anything is wrong. The cost is that a new AMI id changes the launch template, which is what
# `instance_refresh` below is for — the roll is deliberate, not a surprise.
data "aws_ssm_parameter" "ecs_ami" {
  count = local.ec2_capacity
  name  = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}

# ── IAM: an identity for the MACHINE, distinct from the task's ───────────────
#
# The execution role (iam.tf) belongs to a TASK and grants "pull this image, write these logs".
# This one belongs to the INSTANCE and grants what the ECS agent itself needs: register into the
# cluster, report status, pull images for whatever lands on it. Keeping them separate is what
# stops a container inheriting the host's permissions.
data "aws_iam_policy_document" "ec2_assume" {
  count = local.ec2_capacity
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_instance" {
  count              = local.ec2_capacity
  name               = "${local.name_prefix}-ecs-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume[0].json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ecs_instance_agent" {
  count      = local.ec2_capacity
  role       = aws_iam_role.ecs_instance[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

# SSM Session Manager: how you get a shell on a container instance without opening SSH to the
# world. `enable_execute_command` gets you into a CONTAINER; this is for the host underneath —
# which is a thing you now own and will eventually need to look at.
resource "aws_iam_role_policy_attachment" "ecs_instance_ssm" {
  count      = local.ec2_capacity
  role       = aws_iam_role.ecs_instance[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ecs_instance" {
  count = local.ec2_capacity
  name  = "${local.name_prefix}-ecs-instance"
  role  = aws_iam_role.ecs_instance[0].name
  tags  = local.common_tags
}

# ── the instance security group ──────────────────────────────────────────────
#
# COARSER THAN THE FARGATE ONE, AND THAT IS THE TRADE. With `awsvpc` every task carries its own
# ENI and its own security group; under `host` networking the container has no ENI, so this
# group governs everything running on the box. Acceptable for a fleet of our own services;
# it would not be if untrusted code ever shared an instance.
resource "aws_security_group" "ecs_instance" {
  count       = local.ec2_capacity
  name        = "${local.name_prefix}-ecs-instance"
  description = "ECS container instances: service ports from the ALB, all egress"
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.common_tags, { Name = "${local.name_prefix}-ecs-instance" })
}

# WHICH PORTS THE ALB MAY REACH, and it depends on the network mode:
#
#   host   — the container binds the instance's own port, so the ALB uses the same number it
#            always has. One rule per service, exactly like the Fargate group.
#   bridge — ECS assigns each task a RANDOM host port from the ephemeral range and registers
#            that port with the target group. The specific number is unknowable at plan time,
#            so the range is opened instead. This is the security cost of bridge: any port in
#            that range on the instance is reachable from the load balancer — not from the
#            internet, which is what keeps it acceptable.
resource "aws_vpc_security_group_ingress_rule" "ecs_instance_from_alb" {
  for_each = local.ec2_capacity == 1 && var.ec2_network_mode == "host" ? local.services : {}

  security_group_id            = aws_security_group.ecs_instance[0].id
  description                  = "${each.key} from ALB"
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_ingress_rule" "ecs_instance_from_alb_ephemeral" {
  count = local.ec2_capacity == 1 && var.ec2_network_mode == "bridge" ? 1 : 0

  security_group_id            = aws_security_group.ecs_instance[0].id
  description                  = "bridge-mode dynamic host ports from ALB"
  ip_protocol                  = "tcp"
  from_port                    = 32768
  to_port                      = 65535
  referenced_security_group_id = aws_security_group.alb.id
}

# Sibling traffic, matching what the Fargate group already allows: a service on an instance must
# still reach model-proxy and accounts, whichever launch type they happen to be on.
resource "aws_vpc_security_group_ingress_rule" "ecs_instance_from_service" {
  count = local.ec2_capacity

  security_group_id            = aws_security_group.ecs_instance[0].id
  description                  = "inter-service traffic from Fargate tasks"
  ip_protocol                  = "tcp"
  from_port                    = 0
  to_port                      = 65535
  referenced_security_group_id = aws_security_group.service.id
}

resource "aws_vpc_security_group_ingress_rule" "ecs_instance_from_self" {
  count = local.ec2_capacity

  security_group_id            = aws_security_group.ecs_instance[0].id
  description                  = "inter-service traffic between instances"
  ip_protocol                  = "tcp"
  from_port                    = 0
  to_port                      = 65535
  referenced_security_group_id = aws_security_group.ecs_instance[0].id
}

# ALL EGRESS, and it is load-bearing rather than lazy: this instance reaches ECR for images,
# Secrets Manager for config, CloudWatch for logs and Neon for the database — all over the
# public internet, because this VPC has an internet gateway and no NAT.
resource "aws_vpc_security_group_egress_rule" "ecs_instance_all" {
  count = local.ec2_capacity

  security_group_id = aws_security_group.ecs_instance[0].id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ── the launch template ──────────────────────────────────────────────────────
resource "aws_launch_template" "ecs" {
  count       = local.ec2_capacity
  name_prefix = "${local.name_prefix}-ecs-"
  image_id    = data.aws_ssm_parameter.ecs_ami[0].value
  # `instance_type` is where this fleet's whole cost lives; see var.ec2_instance_type.
  instance_type = var.ec2_instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.ecs_instance[0].arn
  }

  # A PUBLIC IP IS MANDATORY HERE, not a convenience. The subnets are public and there is no NAT
  # gateway, and an internet gateway only translates for an interface that HAS a public address.
  # Without this the instance cannot pull its own image, read its secrets, or reach Neon — and
  # the symptom is a container that never starts, with no mention of networking anywhere.
  network_interfaces {
    associate_public_ip_address = true
    security_groups             = [aws_security_group.ecs_instance[0].id]
    delete_on_termination       = true
  }

  # IMDSv2 REQUIRED. The metadata service hands out this instance's role credentials, and the v1
  # protocol answers any process that can make an HTTP request — which is how a request-forgery
  # bug in a container becomes credential theft. `required` forces the token exchange.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # containers are one hop from the host
  }

  # THE LINE THAT JOINS THE CLUSTER. There is no field anywhere on the cluster listing its
  # instances: the agent reads this file at boot and registers itself. Everything else in this
  # file exists to produce a machine that runs it.
  user_data = base64encode(<<-EOT
    #!/bin/bash
    echo "ECS_CLUSTER=${aws_ecs_cluster.main.name}" >> /etc/ecs/ecs.config
    echo "ECS_ENABLE_CONTAINER_METADATA=true" >> /etc/ecs/ecs.config
  EOT
  )

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.common_tags, { Name = "${local.name_prefix}-ecs" })
  }

  tags = local.common_tags

  lifecycle {
    create_before_destroy = true
  }
}

# ── the Auto Scaling Group ───────────────────────────────────────────────────
resource "aws_autoscaling_group" "ecs" {
  count               = local.ec2_capacity
  name                = "${local.name_prefix}-ecs"
  vpc_zone_identifier = aws_subnet.public[*].id

  # MIN ZERO is what makes this cost nothing until a task needs a machine, and what lets the
  # pause switch reach zero. ECS managed scaling moves the number between these bounds.
  min_size = 0
  max_size = local.ec2_max_size

  # REQUIRED BY MANAGED TERMINATION PROTECTION. ECS marks instances that are running tasks as
  # protected and clears the flag when they drain; without this the ASG could terminate a box
  # mid-request during a scale-in.
  protect_from_scale_in = true

  # Roll instances when the launch template changes — which is how a new AMI actually reaches
  # the fleet. `min_healthy_percentage = 100` keeps capacity while it happens.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      # 50, NOT 100, and the difference is whether the roll can start at all. At 100% the group
      # must add a replacement BEFORE retiring anything — which needs a free slot above
      # `desired`, and `desired` reaches `max_size` the moment ECS packs the fleet. The refresh
      # then waits forever for room the ceiling forbids, and an AMI or instance-type change
      # never actually reaches the instances. At 50% it retires one first, so the roll always
      # has somewhere to go; the cost is running at half capacity for a couple of minutes.
      min_healthy_percentage = 50
    }
  }

  # A VERSION NUMBER, NOT "$Latest", and the difference is whether anything ever rolls.
  # "$Latest" is a moving pointer: publishing a new template version leaves this resource's
  # configuration byte-identical, so Terraform reports no change to the ASG — and
  # `instance_refresh` fires only on an ASG change. New instances would pick the new version up,
  # existing ones never would, and nothing would say so. That silently strands a security AMI on
  # the fleet as surely as it stranded the instance-type change here.
  #
  # Referencing latest_version makes the new version part of THIS resource's diff, which is what
  # triggers the roll.
  launch_template {
    id      = aws_launch_template.ecs[0].id
    version = aws_launch_template.ecs[0].latest_version
  }

  # ECS reads this tag to confirm it may manage the group.
  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = false
  }

  dynamic "tag" {
    for_each = local.common_tags
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }

  lifecycle {
    # ECS managed scaling owns this number. Terraform writing it as well would give one value two
    # owners, so state and reality would disagree by design — the same reasoning that removed
    # `ignore_changes = [desired_count]` from the ECS services in services.tf.
    ignore_changes = [desired_capacity]
  }
}

# ── the capacity provider ────────────────────────────────────────────────────
#
# The bridge: it lets ECS add instances when a task cannot be placed, and remove them when they
# are idle. A service opts in via `capacity_provider_strategy`; until one does, this provider
# holds an ASG that sits at zero.
resource "aws_ecs_capacity_provider" "ec2" {
  count = local.ec2_capacity
  name  = "${local.name_prefix}-ec2"

  auto_scaling_group_provider {
    auto_scaling_group_arn = aws_autoscaling_group.ecs[0].arn

    # The safety interlock: ECS protects an instance that is running tasks from scale-in and
    # drains it before releasing it. Requires protect_from_scale_in on the ASG above.
    managed_termination_protection = "ENABLED"

    managed_scaling {
      status = "ENABLED"
      # 100 = pack instances full before adding another, which is the whole economic argument
      # for EC2: a box only pays for itself when several services share it. Lower values keep
      # headroom at the cost of running emptier machines.
      target_capacity           = 100
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 1
    }
  }

  tags = local.common_tags
}

# Associating the provider with the cluster is what makes its name usable in a service's
# `capacity_provider_strategy`. No default strategy is set: every service keeps saying FARGATE
# until it is moved explicitly, so this association cannot reschedule anything by itself.
resource "aws_ecs_cluster_capacity_providers" "main" {
  count              = local.ec2_capacity
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = [aws_ecs_capacity_provider.ec2[0].name]
}

# ── EFS from the instances ───────────────────────────────────────────────────
#
# The daemon mounts EFS for per-account state (agent files, sessions, memory), and the existing
# rule admits only the FARGATE task security group — so the same task on an instance is refused
# at the NFS port. The symptom is a task that fails to start with a mount error, naming neither
# the security group nor EFS in a way that suggests one.
#
# Mounting itself needs nothing extra: the ECS-optimised AMI ships amazon-efs-utils, and the
# access point plus transit encryption in the task definition work identically on EC2.
resource "aws_vpc_security_group_ingress_rule" "efs_from_ecs_instance" {
  count = local.ec2_capacity

  security_group_id            = aws_security_group.efs.id
  description                  = "NFS from ECS container instances"
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
  referenced_security_group_id = aws_security_group.ecs_instance[0].id
}
