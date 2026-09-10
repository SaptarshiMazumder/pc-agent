# HOW MANY COPIES OF A SERVICE EXIST — Application Auto Scaling over the ECS services.
#
# SEPARATE FROM services.tf DELIBERATELY. That file answers "what is this service": its image,
# its ports, its secrets, how it rolls, how it drains. This one answers "how many of it", which
# is a different question with a different clock — it moves minute to minute on a CloudWatch
# alarm, while everything in services.tf moves only when a person deploys.
#
# THIS FILE IS THE SINGLE WRITER OF desired_count. services.tf sets `ignore_changes` on it for
# exactly that reason: with Terraform also writing the number, every `terraform apply` would haul
# a scaled-out service back down to its floor — shedding tasks at the moment load justified them
# — and then show the same diff again on the next run, forever.
#
# TWO LAYERS SCALE HERE, AND THEY ARE NOT THE SAME LAYER:
#
#   this file          more TASKS when a service is busy
#   ec2_capacity.tf    more INSTANCES when the tasks no longer fit
#
# They cooperate only because the pool keeps slack (`ec2_target_capacity` = 80). A task that fits
# in the slack starts in about thirty seconds; one that does not waits three to five minutes for
# an instance to boot, join the cluster and pull the image. Packing the pool to 100% makes the
# second case the normal case, which is why it is not the default any more.

locals {
  # EVERY SERVICE GETS A TARGET, including the ones that never move. That is not symmetry for its
  # own sake: `ignore_changes` takes a static list, so Terraform cannot manage desired_count for
  # some services while ignoring it for others. Rather than let two systems own one number for
  # part of the fleet, Application Auto Scaling owns it for all of it — and a service with no
  # autoscale block simply gets min == max == its fixed count, which pins it exactly where
  # `desired_count` used to.
  #
  # THE DAEMON IS THE POINT OF THAT. It is pinned at 1 by min = max = 1, not by hoping nothing
  # scales it, and `single_writer` services are forbidden an autoscale block by validation.
  scaling_targets = {
    for name, cfg in local.alb_services : name => {
      # PAUSED COLLAPSES BOTH BOUNDS TO ZERO, and this is now the only thing that stops tasks.
      # The cost switch used to work by writing desired_count = 0 in services.tf; that write is
      # ignored now, so the bounds have to do it. Min as well as max: a floor above zero would
      # immediately scale the service back up.
      min = local.paused ? 0 : (cfg.autoscale != null ? cfg.autoscale.min : cfg.desired_count)
      max = local.paused ? 0 : (cfg.autoscale != null ? cfg.autoscale.max : cfg.desired_count)
    }
  }

  # Only the services that actually react to load. A target without a policy is a pin; a target
  # with one is a range.
  scaling_policies = {
    for name, cfg in local.alb_services : name => cfg.autoscale
    if cfg.autoscale != null
  }
}

# ── the range each service may move within ───────────────────────────────────
resource "aws_appautoscaling_target" "svc" {
  for_each = local.scaling_targets

  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.svc[each.key].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = each.value.min
  max_capacity       = each.value.max
}

# ── what makes it move ───────────────────────────────────────────────────────
#
# TARGET TRACKING, not step scaling: the policy is handed one number to hold and it works out
# the alarms and the arithmetic itself. Step scaling would mean choosing thresholds and
# increments per service, which is a lot of tuning to get a worse answer.
resource "aws_appautoscaling_policy" "svc" {
  for_each = local.scaling_policies

  name               = "${local.name_prefix}-${each.key}-${each.value.metric}"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.svc[each.key].service_namespace
  resource_id        = aws_appautoscaling_target.svc[each.key].resource_id
  scalable_dimension = aws_appautoscaling_target.svc[each.key].scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value = each.value.target

    # ASYMMETRIC ON PURPOSE. Scaling out is cheap and reversible; scaling in throws away a warm
    # task that may be needed again in a minute, and on EC2 it can also strand a half-empty
    # instance. So: react to a rise in one minute, wait five before believing a fall. Anything
    # closer to symmetric flaps, and flapping under load is worse than being slightly too big.
    scale_out_cooldown = 60
    scale_in_cooldown  = 300

    # CPU — for work that actually burns the processor. accounts is the case: sign-in runs
    # PBKDF2 at 200k rounds, so utilisation genuinely tracks the load.
    dynamic "predefined_metric_specification" {
      for_each = each.value.metric == "cpu" ? [1] : []
      content {
        predefined_metric_type = "ECSServiceAverageCPUUtilization"
      }
    }

    # REQUESTS PER TARGET — for anything the load balancer fronts, and the right default for
    # every I/O-bound service here. The model proxy is the illustration: it spends its life
    # blocked on somebody else's API, so it can hold every connection it has while reporting
    # single-digit CPU. A CPU policy would watch that queue build and call it idle.
    #
    # `resource_label` is how CloudWatch is told WHICH target group's traffic to divide: the
    # ALB's arn_suffix and the target group's, joined. Not an ARN, and not the name.
    dynamic "predefined_metric_specification" {
      for_each = each.value.metric == "alb_requests" ? [1] : []
      content {
        predefined_metric_type = "ALBRequestCountPerTarget"
        resource_label         = "${local.alb_suffix}/${aws_lb_target_group.svc[each.key].arn_suffix}"
      }
    }
  }
}
