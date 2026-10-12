# Adopt surviving production IAM identities and signing-key encryption material.
# No roles or keys are deleted/recreated during the production rebuild.
import {
  to = module.stack.aws_iam_role.builder
  id = "agentd-production-builder"
}
import {
  to = module.stack.aws_iam_role.execution
  id = "agentd-production-ecs-execution"
}
import {
  to = module.stack.aws_iam_role.ecs_host_watchdog
  id = "agentd-production-ecs-host-watchdog"
}
import {
  to = module.stack.aws_iam_role.ecs_instance[0]
  id = "agentd-production-ecs-instance"
}
import {
  to = module.stack.aws_iam_role.task
  id = "agentd-production-ecs-task"
}
import {
  to = module.stack.aws_iam_role.task_admin
  id = "agentd-production-ecs-task-admin"
}
import {
  to = module.stack.aws_iam_role.executor
  id = "agentd-production-executor"
}
import {
  to = module.stack.aws_iam_role.publish
  id = "agentd-production-publish"
}
import {
  to = module.stack.aws_iam_role.sandbox_reaper
  id = "agentd-production-runtime-reaper"
}
import {
  to = module.stack.aws_iam_role.sandbox_execution
  id = "agentd-production-sandbox-execution"
}
import {
  to = module.stack.aws_iam_role.scheduled_jobs
  id = "agentd-production-scheduled-jobs"
}
import {
  to = module.stack.aws_iam_role.scheduler
  id = "agentd-production-scheduler"
}
import {
  to = module.stack.aws_kms_key.publish
  id = "d3c6bef3-fe80-4e43-a93d-030f308279d5"
}
import {
  to = module.stack.aws_kms_alias.publish
  id = "alias/agentd-production-publish"
}
