"""Install the private power controller without relying on the lost Terraform state.

Reads existing resources; does not start/stop the application during installation.
"""
import argparse
import io
import json
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from controller import dns_points_to, normalized_dns_name


def statement(actions, resources, **extra):
    return {"Effect": "Allow", "Action": actions, "Resource": resources, **extra}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="ap-northeast-1")
    parser.add_argument("--stack", default="agentd-staging", help="Existing sole environment; legacy AWS name")
    parser.add_argument("--account", default="891612568944")
    parser.add_argument("--timezone", default="Asia/Tokyo")
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    client = session.client
    identity = client("sts").get_caller_identity()
    if identity["Account"] != args.account:
        raise RuntimeError("Wrong AWS account; refusing installation")
    base = f"arn:aws:{{service}}:{args.region}:{args.account}:"
    name = args.stack + "-power"
    bucket = f"agentd-power-{args.account}-{args.region}"
    parameter = "/agentd/power/" + args.stack + "/mode"
    schedule_name = name + "-off-0200"
    fn_arn = base.format(service="lambda") + "function:" + name
    iam = client("iam")
    ecs = client("ecs")
    if not ecs.describe_clusters(clusters=[args.stack])["clusters"]:
        raise RuntimeError("The target ECS cluster does not exist")
    lb = client("elbv2").describe_load_balancers(Names=[args.stack])["LoadBalancers"][0]
    zones = []
    dns = []
    for page in client("route53").get_paginator("list_hosted_zones").paginate():
        for zone in page["HostedZones"]:
            if zone["Config"].get("PrivateZone"):
                continue
            for records in client("route53").get_paginator("list_resource_record_sets").paginate(HostedZoneId=zone["Id"]):
                for record in records["ResourceRecordSets"]:
                    if dns_points_to(record, lb["DNSName"]):
                        zones.append("arn:aws:route53:::" + zone["Id"].lstrip("/"))
                        dns.append(normalized_dns_name(record["Name"]))
    if not dns:
        raise RuntimeError("No stable Route53 hostname points to the load balancer")
    scheduler = client("scheduler")
    job_roles = set()
    for page in scheduler.get_paginator("list_schedules").paginate(NamePrefix=args.stack + "-"):
        for schedule in page["Schedules"]:
            if schedule["Name"] != schedule_name:
                full = scheduler.get_schedule(Name=schedule["Name"], GroupName=schedule["GroupName"])
                job_roles.add(full["Target"]["RoleArn"])
    job_functions = [f["FunctionArn"] for page in client("lambda").get_paginator("list_functions").paginate()
                     for f in page["Functions"]
                     if f["FunctionName"].startswith(args.stack + "-") and f["FunctionName"] != name]
    scaling_targets = [target["ScalableTargetARN"] for page in client("application-autoscaling").get_paginator("describe_scalable_targets").paginate(ServiceNamespace="ecs")
                       for target in page["ScalableTargets"] if target["ResourceId"].startswith("service/" + args.stack + "/")]

    s3 = client("s3")
    try:
        s3.head_bucket(Bucket=bucket, ExpectedBucketOwner=args.account)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            raise
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": args.region})
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True,
        "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
    })
    s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={"Rules": [
        {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
    ]})
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Deny", "Principal": "*", "Action": "s3:*",
        "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
    }]}))
    s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": [{
        "ID": "expire-old-state-versions", "Status": "Enabled", "Filter": {"Prefix": ""},
        "NoncurrentVersionExpiration": {"NoncurrentDays": 30, "NewerNoncurrentVersions": 10},
    }]})
    ssm = client("ssm")
    try:
        ssm.get_parameter(Name=parameter)
    except ssm.exceptions.ParameterNotFound:
        ssm.put_parameter(Name=parameter, Type="String", Value="automatic",
                          Description="automatic: nightly OFF; always-on: skip nightly OFF. Neither starts compute.")

    def role(role_name, service, policy, conditions=None):
        trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
            "Principal": {"Service": service}, "Action": "sts:AssumeRole",
            **({"Condition": conditions} if conditions else {}),
        }]}
        try:
            arn = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust),
                                  Tags=[{"Key": "Project", "Value": "agentd"}])["Role"]["Arn"]
        except iam.exceptions.EntityAlreadyExistsException:
            arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
            iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust))
        iam.put_role_policy(RoleName=role_name, PolicyName="agentd-power", PolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": policy,
        }))
        return arn

    cluster_arn = base.format(service="ecs") + "cluster/" + args.stack
    elb_base = base.format(service="elasticloadbalancing")
    elb_resources = [elb_base + resource for resource in (
        f"loadbalancer/app/{args.stack}/*", f"listener/app/{args.stack}/*/*",
        f"listener-rule/app/{args.stack}/*/*/*", f"targetgroup/{args.stack}-*/*",
    )]
    policy = [
        statement(["ecs:DescribeClusters", "ecs:DescribeServices", "ecs:ListServices", "ecs:ListTasks",
                   "ecs:DescribeTasks", "ecs:DescribeCapacityProviders", "autoscaling:DescribeAutoScalingGroups",
                   "elasticloadbalancing:Describe*", "route53:ListHostedZones", "route53:ListResourceRecordSets",
                   "scheduler:ListSchedules", "scheduler:GetSchedule", "events:ListRules", "lambda:ListFunctions"], "*"),
        statement("application-autoscaling:DescribeScalableTargets", "*"),
        statement(["s3:GetObject", "s3:PutObject"], [f"arn:aws:s3:::{bucket}/{key}" for key in ("restore.json", "state.json", "last-on.json")]),
        statement("s3:ListBucket", f"arn:aws:s3:::{bucket}"),
        statement("ssm:GetParameter", base.format(service="ssm") + "parameter" + parameter),
        statement("ecs:UpdateService", base.format(service="ecs") + f"service/{args.stack}/*"),
        statement("ecs:StopTask", base.format(service="ecs") + f"task/{args.stack}/*",
                  Condition={"ArnEquals": {"ecs:cluster": cluster_arn}}),
        statement("ecs:UpdateCapacityProvider", base.format(service="ecs") + f"capacity-provider/{args.stack}-*"),
        statement(["autoscaling:UpdateAutoScalingGroup", "autoscaling:SetInstanceProtection"],
                  base.format(service="autoscaling") + f"autoScalingGroup:*:autoScalingGroupName/{args.stack}-*"),
        statement("elasticloadbalancing:CreateLoadBalancer", "*",
                  Condition={"StringEquals": {"aws:RequestTag/Project": "agentd"}}),
        statement(["elasticloadbalancing:DeleteLoadBalancer", "elasticloadbalancing:ModifyLoadBalancerAttributes",
                   "elasticloadbalancing:CreateListener", "elasticloadbalancing:ModifyListener",
                   "elasticloadbalancing:AddListenerCertificates", "elasticloadbalancing:CreateRule",
                   "elasticloadbalancing:ModifyRule", "elasticloadbalancing:AddTags"], elb_resources),
        statement("route53:ChangeResourceRecordSets", sorted(set(zones)), Condition={"ForAllValues:StringEquals": {
            "route53:ChangeResourceRecordSetsNormalizedRecordNames": sorted(set(dns)),
            "route53:ChangeResourceRecordSetsActions": ["UPSERT"],
            "route53:ChangeResourceRecordSetsRecordTypes": ["A", "AAAA", "CNAME"],
        }}),
        statement("scheduler:UpdateSchedule", base.format(service="scheduler") + f"schedule/*/{args.stack}-*"),
        statement(["events:EnableRule", "events:DisableRule"], base.format(service="events") + f"rule/{args.stack}-*"),
        statement(["logs:CreateLogStream", "logs:PutLogEvents"], base.format(service="logs") + f"log-group:/aws/lambda/{name}:*"),
    ]
    if job_functions:
        policy.append(statement(["lambda:GetFunctionConcurrency", "lambda:PutFunctionConcurrency", "lambda:DeleteFunctionConcurrency"], job_functions))
    if scaling_targets:
        policy.append(statement("application-autoscaling:RegisterScalableTarget", scaling_targets))
    if job_roles:
        policy.append(statement("iam:PassRole", sorted(job_roles), Condition={"StringEquals": {"iam:PassedToService": "scheduler.amazonaws.com"}}))
    execution_role = role(name, "lambda.amazonaws.com", policy)
    scheduler_role = role(name + "-schedule", "scheduler.amazonaws.com", [statement("lambda:InvokeFunction", fn_arn)],
                          {"StringEquals": {"aws:SourceAccount": args.account}, "ArnLike": {
                              "aws:SourceArn": base.format(service="scheduler") + "schedule-group/default"}})
    logs = client("logs")
    try:
        logs.create_log_group(logGroupName="/aws/lambda/" + name)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    logs.put_retention_policy(logGroupName="/aws/lambda/" + name, retentionInDays=14)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(Path(__file__).with_name("controller.py"), "controller.py")
    lamb = client("lambda")
    config = dict(FunctionName=name, Role=execution_role, Runtime="python3.12", Handler="controller.handler",
                  Timeout=900, MemorySize=256, Environment={"Variables": {
                      "POWER_REGION": args.region, "POWER_ACCOUNT": args.account,
                      "POWER_STACK": args.stack, "POWER_BUCKET": bucket,
                      "POWER_MODE_PARAMETER": parameter, "POWER_FUNCTION": name,
                      "POWER_SCHEDULE": schedule_name,
                  }})
    for attempt in range(12):
        try:
            try:
                lamb.get_function(FunctionName=name)
            except lamb.exceptions.ResourceNotFoundException:
                lamb.create_function(**config, Code={"ZipFile": stream.getvalue()}, Tags={"Project": "agentd"})
                lamb.get_waiter("function_active_v2").wait(FunctionName=name)
            else:
                lamb.update_function_configuration(**config)
                lamb.get_waiter("function_updated_v2").wait(FunctionName=name)
                lamb.update_function_code(FunctionName=name, ZipFile=stream.getvalue())
                lamb.get_waiter("function_updated_v2").wait(FunctionName=name)
            break
        except lamb.exceptions.InvalidParameterValueException as exc:
            if "cannot be assumed" not in str(exc) or attempt == 11:
                raise
            time.sleep(5)
    # Serial lifecycle operations; no concurrent ON can race a nightly OFF.
    lamb.put_function_concurrency(FunctionName=name, ReservedConcurrentExecutions=1)
    lamb.put_function_event_invoke_config(FunctionName=name, MaximumRetryAttempts=0, MaximumEventAgeInSeconds=1800)
    for attempt in range(12):
        capture = lamb.invoke(FunctionName=name, Payload=json.dumps({"action": "capture"}).encode())
        result = json.loads(capture["Payload"].read())
        if not capture.get("FunctionError"):
            break
        # Newly installed IAM permissions can take time to propagate to services.
        if "AccessDenied" not in result.get("errorMessage", "") or attempt == 11:
            raise RuntimeError("Recovery snapshot failed; nightly schedule was NOT enabled: " + str(result))
        time.sleep(5)
    target = {"Arn": fn_arn, "RoleArn": scheduler_role,
              "Input": json.dumps({"action": "scheduled-off", "scheduled_at": "<aws.scheduler.scheduled-time>"}),
              "RetryPolicy": {"MaximumRetryAttempts": 0, "MaximumEventAgeInSeconds": 1800}}
    schedule = dict(Name=schedule_name, GroupName="default", ScheduleExpression="cron(0 2 * * ? *)",
                    ScheduleExpressionTimezone=args.timezone, FlexibleTimeWindow={"Mode": "OFF"},
                    State="ENABLED", Target=target, Description="Agentd nightly OFF. No automatic ON. Always-on mode skips OFF.")
    try:
        scheduler.create_schedule(**schedule)
    except scheduler.exceptions.ConflictException:
        scheduler.update_schedule(**schedule)
    client("cloudwatch").put_metric_alarm(
        AlarmName=name + "-shutdown-errors", AlarmDescription="Inspect power status/logs if nightly shutdown fails",
        Namespace="AWS/Lambda", MetricName="Errors", Dimensions=[{"Name": "FunctionName", "Value": name}],
        Statistic="Sum", Period=300, EvaluationPeriods=1, Threshold=1,
        ComparisonOperator="GreaterThanOrEqualToThreshold", TreatMissingData="notBreaching",
    )
    print(json.dumps({"installed": name, "shutdown": "02:00 " + args.timezone,
                      "mode": ssm.get_parameter(Name=parameter)["Parameter"]["Value"],
                      "automatic_start": False, "recovery": result}, indent=2))


if __name__ == "__main__":
    main()
