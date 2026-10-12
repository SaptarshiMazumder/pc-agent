"""Private AWS lifecycle controller. Only an explicit `on` invocation starts compute."""

import copy
import datetime as dt
import json
import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters


def scheduled_action(event, mode, now=None, last_manual_on=None):
    """Never execute a delayed nightly OFF against a subsequent manual ON."""
    if event.get("action") != "scheduled-off":
        return event.get("action")
    stamp = event.get("scheduled_at")
    if not stamp:
        raise ValueError("scheduled-off requires the scheduler timestamp")
    scheduled = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if scheduled.tzinfo is None:
        raise ValueError("scheduler timestamp must include a timezone")
    age = ((now or dt.datetime.now(dt.UTC)) - scheduled).total_seconds()
    if age < -60 or age > 1800:
        return "skip-stale"
    if last_manual_on:
        manual = dt.datetime.fromisoformat(last_manual_on.replace("Z", "+00:00"))
        if manual > scheduled:
            return "skip-new-manual-session"
    return "skip-keep-on" if mode == "always-on" else "off"


def dns_points_to(record, hostname):
    name = hostname.lower().rstrip(".")
    if "AliasTarget" in record:
        return record["AliasTarget"]["DNSName"].lower().rstrip(".") == name
    return record["Type"] == "CNAME" and any(
        value["Value"].lower().rstrip(".") == name
        for value in record.get("ResourceRecords", [])
    )


def normalized_dns_name(name):
    # IAM's exact-name condition uses octal escapes, not a literal domain wildcard.
    return name.replace("*", "\\052").rstrip(".").lower()


def restored_record(record, lb):
    value = copy.deepcopy(record)
    if "AliasTarget" in value:
        value["AliasTarget"].update(
            DNSName=lb["DNSName"], HostedZoneId=lb["CanonicalHostedZoneId"]
        )
    else:
        value["ResourceRecords"] = [{"Value": lb["DNSName"]}]
    return value


class Controller:
    def __init__(self, clients=None, sleep=time.sleep):
        self.region = os.environ["POWER_REGION"]
        self.account = os.environ["POWER_ACCOUNT"]
        self.prefix = os.environ["POWER_STACK"]
        self.cluster = self.prefix
        self.bucket = os.environ["POWER_BUCKET"]
        self.mode_parameter = os.environ["POWER_MODE_PARAMETER"]
        self.function_name = os.environ["POWER_FUNCTION"]
        self.schedule_name = os.environ["POWER_SCHEDULE"]
        self.sleep = sleep
        self.clients = clients or {}

    def client(self, name):
        if name not in self.clients:
            self.clients[name] = boto3.client(
                name, region_name=self.region,
                config=Config(retries={"max_attempts": 6, "mode": "standard"}),
            )
        return self.clients[name]

    def all(self, service, operation, key, **kwargs):
        client = self.client(service)
        if client.can_paginate(operation):
            return [item for page in client.get_paginator(operation).paginate(**kwargs)
                    for item in page.get(key, [])]
        return getattr(client, operation)(**kwargs).get(key, [])

    def request(self, service, operation, data):
        """Drop response-only fields using the SDK's input schema, recursively."""
        shape = self.client(service).meta.service_model.operation_model(operation).input_shape

        def clean(value, schema):
            if schema.type_name == "structure":
                return {k: clean(v, schema.members[k]) for k, v in value.items()
                        if k in schema.members and k != "IsDefault"}
            if schema.type_name == "list":
                return [clean(v, schema.member) for v in value]
            if schema.type_name == "map":
                return {k: clean(v, schema.value) for k, v in value.items()}
            return value

        return clean(data, shape)

    def read(self, key, default=None):
        try:
            raw = self.client("s3").get_object(Bucket=self.bucket, Key=key)["Body"].read()
            return json.loads(raw)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return default
            raise

    def write(self, key, value):
        body = json.dumps(value, default=str, sort_keys=True).encode()
        self.client("s3").put_object(
            Bucket=self.bucket, Key=key, Body=body,
            ContentType="application/json", ServerSideEncryption="AES256",
        )
        # A failed or unverified recovery manifest must never permit teardown.
        if self.read(key) != json.loads(body):
            raise RuntimeError("Could not verify saved power state")

    def set_state(self, phase, **details):
        value = {"phase": phase, "updated_at": dt.datetime.now(dt.UTC).isoformat(),
                 **details}
        self.write("state.json", value)
        return value

    def mode(self):
        value = self.client("ssm").get_parameter(Name=self.mode_parameter)["Parameter"]["Value"]
        if value not in ("automatic", "always-on"):
            raise RuntimeError("Unknown power mode; refusing automatic shutdown")
        return value

    def lb(self):
        try:
            return self.client("elbv2").describe_load_balancers(Names=[self.prefix])["LoadBalancers"][0]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "LoadBalancerNotFound":
                return None
            raise

    def services(self):
        arns = self.all("ecs", "list_services", "serviceArns", cluster=self.cluster)
        result = []
        for offset in range(0, len(arns), 10):
            response = self.client("ecs").describe_services(
                cluster=self.cluster, services=arns[offset:offset + 10]
            )
            if response.get("failures"):
                raise RuntimeError("Could not inspect all ECS services")
            result.extend(response["services"])
        return result

    def validate(self, manifest):
        if not manifest or manifest.get("version") != 1:
            raise RuntimeError("No valid recovery manifest; run capture while the stack is ON")
        if (manifest["account"], manifest["region"], manifest["stack"]) != (
            self.account, self.region, self.prefix
        ):
            raise RuntimeError("Recovery manifest belongs to another AWS stack")
        if not manifest.get("listeners") or not manifest.get("dns"):
            raise RuntimeError("Recovery manifest lacks load-balancer listeners or stable DNS")
        for listener in manifest["listeners"]:
            for action in listener["config"]["DefaultActions"] + [
                action for rule in listener["rules"] for action in rule["Actions"]
            ]:
                if action["Type"].startswith("authenticate-"):
                    raise RuntimeError("Authentication listeners need a separate secure recovery mechanism")
        if any(a["Key"] == "deletion_protection.enabled" and a["Value"] == "true"
               for a in manifest["attributes"]):
            raise RuntimeError("Load balancer deletion protection is enabled")
        return manifest

    def validate_requests(self, manifest):
        """Validate the full saved restoration against AWS input schemas before OFF."""
        elb = self.client("elbv2")
        requests = [("CreateLoadBalancer", manifest["load_balancer"])]
        for listener in manifest["listeners"]:
            config = listener["config"]
            requests.append(("CreateListener", config))
            for rule in listener["rules"]:
                requests.append(("CreateRule", dict(rule, ListenerArn=config["ListenerArn"], Priority=int(rule["Priority"]))))
        for operation, value in requests:
            validate_parameters(self.request("elbv2", operation, value),
                                elb.meta.service_model.operation_model(operation).input_shape)

    def capture(self):
        phase = self.read("state.json", {}).get("phase")
        if phase in ("off", "stopping", "starting", "error-off", "error-on"):
            # Repeated OFF must not replace nonzero recovery counts with zeroes.
            return self.validate(self.read("restore.json"))
        lb = self.lb()
        if not lb:
            return self.validate(self.read("restore.json"))
        elb = self.client("elbv2")
        manifest = {
            "version": 1, "account": self.account, "region": self.region,
            "stack": self.prefix,
            "captured_at": dt.datetime.now(dt.UTC).isoformat(),
            "load_balancer": {
                "Name": self.prefix, "Scheme": lb["Scheme"], "Type": lb["Type"],
                "IpAddressType": lb["IpAddressType"], "SecurityGroups": lb["SecurityGroups"],
                "Subnets": [az["SubnetId"] for az in lb["AvailabilityZones"]],
                "Tags": elb.describe_tags(ResourceArns=[lb["LoadBalancerArn"]])["TagDescriptions"][0]["Tags"],
            },
            "attributes": elb.describe_load_balancer_attributes(LoadBalancerArn=lb["LoadBalancerArn"])["Attributes"],
            "listeners": [], "dns": [], "services": [], "groups": [],
            "providers": [], "schedules": [], "rules": [], "functions": [],
            "service_scaling": [],
        }
        for li in self.all("elbv2", "describe_listeners", "Listeners", LoadBalancerArn=lb["LoadBalancerArn"]):
            extra_certs = []
            if li.get("Certificates"):
                extra_certs = self.all("elbv2", "describe_listener_certificates", "Certificates", ListenerArn=li["ListenerArn"])
            rules = [r for r in self.all("elbv2", "describe_rules", "Rules", ListenerArn=li["ListenerArn"])
                     if not r["IsDefault"]]
            manifest["listeners"].append({"config": li, "certificates": extra_certs, "rules": rules})
        for zone in self.all("route53", "list_hosted_zones", "HostedZones"):
            if zone["Config"].get("PrivateZone"):
                continue
            for record in self.all("route53", "list_resource_record_sets", "ResourceRecordSets", HostedZoneId=zone["Id"]):
                if dns_points_to(record, lb["DNSName"]):
                    manifest["dns"].append({"zone": zone["Id"], "record": record})
        services = self.services()
        manifest["services"] = [{"name": s["serviceName"], "desired": s["desiredCount"]} for s in services]
        manifest["service_scaling"] = [target for target in self.all(
            "application-autoscaling", "describe_scalable_targets", "ScalableTargets", ServiceNamespace="ecs"
        ) if target["ResourceId"].startswith("service/" + self.cluster + "/")]
        cluster = self.client("ecs").describe_clusters(clusters=[self.cluster])["clusters"][0]
        provider_names = [p for p in cluster.get("capacityProviders", []) if p.startswith(self.prefix + "-")]
        if provider_names:
            for p in self.client("ecs").describe_capacity_providers(capacityProviders=provider_names)["capacityProviders"]:
                manifest["providers"].append({"name": p["name"], "config": p["autoScalingGroupProvider"]})
        groups = self.all("autoscaling", "describe_auto_scaling_groups", "AutoScalingGroups")
        for group in groups:
            if group["AutoScalingGroupName"].startswith(self.prefix + "-"):
                manifest["groups"].append({k: group[k] for k in (
                    "AutoScalingGroupName", "MinSize", "MaxSize", "DesiredCapacity"
                )})
        for sch in self.all("scheduler", "list_schedules", "Schedules", NamePrefix=self.prefix + "-"):
            if sch["Name"] != self.schedule_name:
                manifest["schedules"].append(self.client("scheduler").get_schedule(Name=sch["Name"], GroupName=sch["GroupName"]))
        manifest["rules"] = self.all("events", "list_rules", "Rules", NamePrefix=self.prefix + "-")
        for fn in self.all("lambda", "list_functions", "Functions"):
            if fn["FunctionName"].startswith(self.prefix + "-") and fn["FunctionName"] != self.function_name:
                concurrency = self.client("lambda").get_function_concurrency(FunctionName=fn["FunctionName"])
                manifest["functions"].append({"name": fn["FunctionName"], "reserved": concurrency.get("ReservedConcurrentExecutions")})
        self.validate(manifest)
        self.validate_requests(manifest)
        self.write("restore.json", manifest)
        return manifest

    def set_jobs(self, manifest, enabled):
        for fn in manifest["functions"]:
            if enabled and fn["reserved"] is None:
                self.client("lambda").delete_function_concurrency(FunctionName=fn["name"])
            else:
                self.client("lambda").put_function_concurrency(
                    FunctionName=fn["name"], ReservedConcurrentExecutions=fn["reserved"] if enabled else 0,
                )
        for schedule in manifest["schedules"]:
            # Use the latest definition, preserving changes made to its timing/target.
            current = self.client("scheduler").get_schedule(Name=schedule["Name"], GroupName=schedule["GroupName"])
            current["State"] = schedule["State"] if enabled else "DISABLED"
            self.client("scheduler").update_schedule(**self.request("scheduler", "UpdateSchedule", current))
        for rule in manifest["rules"]:
            method = "enable_rule" if enabled and rule["State"].startswith("ENABLED") else "disable_rule"
            getattr(self.client("events"), method)(Name=rule["Name"], EventBusName=rule.get("EventBusName", "default"))

    def set_providers(self, manifest, enabled):
        ecs = self.client("ecs")
        def wait_for_update(name=None):
            deadline = time.monotonic() + 180
            while True:
                cluster = ecs.describe_clusters(clusters=[self.cluster], include=["ATTACHMENTS"])["clusters"][0]
                updating = cluster.get("attachmentsStatus", "").endswith("IN_PROGRESS")
                if name:
                    provider = ecs.describe_capacity_providers(capacityProviders=[name])["capacityProviders"][0]
                    if provider.get("updateStatus") == "UPDATE_FAILED":
                        raise RuntimeError("ECS capacity provider update failed: " + provider.get("updateStatusReason", name))
                    updating = updating or provider.get("updateStatus") == "UPDATE_IN_PROGRESS"
                if not updating:
                    return
                if time.monotonic() > deadline:
                    raise RuntimeError("ECS capacity provider update did not complete")
                self.sleep(5)

        # ECS serializes capacity-provider attachment updates at the cluster level.
        # Updating both providers without waiting makes the second update fail.
        for provider in manifest["providers"]:
            wait_for_update()
            config = copy.deepcopy(provider["config"])
            if not enabled:
                config["managedScaling"]["status"] = "DISABLED"
                config["managedTerminationProtection"] = "DISABLED"
            ecs.update_capacity_provider(**self.request("ecs", "UpdateCapacityProvider", {
                "name": provider["name"], "autoScalingGroupProvider": config,
            }))
            wait_for_update(provider["name"])

    def set_service_scaling(self, manifest, enabled):
        for target in manifest.get("service_scaling", []):
            self.client("application-autoscaling").register_scalable_target(
                ServiceNamespace="ecs", ResourceId=target["ResourceId"],
                ScalableDimension=target["ScalableDimension"],
                MinCapacity=target["MinCapacity"] if enabled else 0,
                MaxCapacity=target["MaxCapacity"] if enabled else 0,
                SuspendedState=target["SuspendedState"] if enabled else {
                    "DynamicScalingInSuspended": True, "DynamicScalingOutSuspended": True,
                    "ScheduledScalingSuspended": True,
                },
            )

    def off(self, nightly=False):
        if nightly and self.mode() == "always-on":
            return {"result": "skip-keep-on"}
        manifest = self.capture()
        self.set_state("stopping")
        self.set_jobs(manifest, False)
        self.set_providers(manifest, False)
        self.set_service_scaling(manifest, False)
        # Remember tasks before desired counts change; STOPPING tasks disappear from
        # list_tasks(desiredStatus=RUNNING) before their containers have actually exited.
        observed = set(self.all("ecs", "list_tasks", "taskArns", cluster=self.cluster, desiredStatus="RUNNING"))
        for service in self.services():
            self.client("ecs").update_service(cluster=self.cluster, service=service["serviceName"], desiredCount=0)
        deadline = time.monotonic() + 420
        stopping = set()
        while True:
            tasks = self.all("ecs", "list_tasks", "taskArns", cluster=self.cluster, desiredStatus="RUNNING")
            observed.update(tasks)
            active = []
            stopped = list(observed)
            for offset in range(0, len(stopped), 100):
                response = self.client("ecs").describe_tasks(cluster=self.cluster, tasks=stopped[offset:offset + 100])
                active.extend(t for t in response["tasks"] if t["lastStatus"] != "STOPPED")
                if response.get("failures"):
                    raise RuntimeError("Could not verify all tasks have stopped")
            # ECS services may already be draining. Stop remaining standalone jobs,
            # then wait for actual container exit (including the ALB drain interval).
            for task in active:
                arn = task["taskArn"]
                if task["desiredStatus"] == "RUNNING" and arn not in stopping:
                    self.client("ecs").stop_task(cluster=self.cluster, task=arn, reason="Agentd manual/nightly shutdown")
                    stopping.add(arn)
            if not tasks and not active:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Tasks have not stopped; preserving hosts for recovery")
            self.sleep(10)
        for group in manifest["groups"]:
            name = group["AutoScalingGroupName"]
            current = self.client("autoscaling").describe_auto_scaling_groups(AutoScalingGroupNames=[name])["AutoScalingGroups"][0]
            ids = [i["InstanceId"] for i in current["Instances"] if i["ProtectedFromScaleIn"]]
            if ids:
                self.client("autoscaling").set_instance_protection(AutoScalingGroupName=name, InstanceIds=ids, ProtectedFromScaleIn=False)
            self.client("autoscaling").update_auto_scaling_group(AutoScalingGroupName=name, MinSize=0, MaxSize=0, DesiredCapacity=0)
        lb = self.lb()
        if lb:
            self.client("elbv2").delete_load_balancer(LoadBalancerArn=lb["LoadBalancerArn"])
        return self.set_state("off", note="Compute is scaling to zero; persistent data retained")

    def restore_lb(self, manifest):
        elb = self.client("elbv2")
        lb = self.lb() or elb.create_load_balancer(**manifest["load_balancer"])["LoadBalancers"][0]
        # Built-in immutable/default attributes need not be written back.
        attrs = [a for a in manifest["attributes"] if a["Value"] != "" and a["Key"] != "load_balancing.cross_zone.enabled"]
        elb.modify_load_balancer_attributes(LoadBalancerArn=lb["LoadBalancerArn"], Attributes=attrs)
        existing = {li["Port"]: li for li in self.all("elbv2", "describe_listeners", "Listeners", LoadBalancerArn=lb["LoadBalancerArn"])}
        for saved in manifest["listeners"]:
            config = copy.deepcopy(saved["config"])
            config["LoadBalancerArn"] = lb["LoadBalancerArn"]
            if config["Port"] in existing:
                listener = existing[config["Port"]]
                config["ListenerArn"] = listener["ListenerArn"]
                elb.modify_listener(**self.request("elbv2", "ModifyListener", config))
            else:
                listener = elb.create_listener(**self.request("elbv2", "CreateListener", config))["Listeners"][0]
            for cert in saved["certificates"]:
                if not cert["IsDefault"]:
                    elb.add_listener_certificates(ListenerArn=listener["ListenerArn"], Certificates=[{"CertificateArn": cert["CertificateArn"]}])
            rules = {r["Priority"]: r for r in self.all("elbv2", "describe_rules", "Rules", ListenerArn=listener["ListenerArn"])}
            for rule in saved["rules"]:
                value = dict(rule, ListenerArn=listener["ListenerArn"], Priority=int(rule["Priority"]))
                if rule["Priority"] in rules:
                    value["RuleArn"] = rules[rule["Priority"]]["RuleArn"]
                    elb.modify_rule(**self.request("elbv2", "ModifyRule", value))
                else:
                    elb.create_rule(**self.request("elbv2", "CreateRule", value))
        for saved in manifest["dns"]:
            self.client("route53").change_resource_record_sets(
                HostedZoneId=saved["zone"], ChangeBatch={"Comment": "Agentd manual ON: retain hostname",
                    "Changes": [{"Action": "UPSERT", "ResourceRecordSet": restored_record(saved["record"], lb)}]},
            )
        return lb

    def on(self):
        if self.prefix.endswith("-staging"):
            raise RuntimeError("Staging is retired. Only production may be started.")
        manifest = self.validate(self.read("restore.json"))
        # Keep this independent of lifecycle state updates, so a queued nightly
        # OFF cannot interrupt an explicit manual start made after its scheduled time.
        self.write("last-on.json", {"requested_at": dt.datetime.now(dt.UTC).isoformat()})
        self.set_state("starting")
        # Target groups must be associated with an ALB before ECS can resume.
        lb = self.restore_lb(manifest)
        for group in manifest["groups"]:
            self.client("autoscaling").update_auto_scaling_group(**group)
        self.set_providers(manifest, True)
        for service in manifest["services"]:
            self.client("ecs").update_service(cluster=self.cluster, service=service["name"], desiredCount=service["desired"])
        deadline = time.monotonic() + 480
        while True:
            services = self.services()
            desired = {s["name"]: s["desired"] for s in manifest["services"]}
            if all(s["runningCount"] == desired.get(s["serviceName"], 0) and s["pendingCount"] == 0
                   and all(d["rolloutState"] == "COMPLETED" for d in s.get("deployments", []) if "rolloutState" in d)
                   for s in services):
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Startup did not become healthy; run status and retry on. Background jobs remain disabled.")
            self.sleep(10)
        self.set_jobs(manifest, True)
        self.set_service_scaling(manifest, True)
        return self.set_state("on", hostnames=[d["record"]["Name"] for d in manifest["dns"]], load_balancer=lb["DNSName"])

    def status(self):
        return {"mode": self.mode(), "state": self.read("state.json", {"phase": "unrecorded"}),
                "load_balancer_present": bool(self.lb()),
                "services": [{"name": s["serviceName"], "desired": s["desiredCount"],
                              "running": s["runningCount"], "pending": s["pendingCount"]} for s in self.services()],
                "shutdown": "02:00 Asia/Tokyo", "automatic_start": False}


def handler(event, context):
    control = Controller()
    nightly = event.get("action") == "scheduled-off"
    last_on = control.read("last-on.json", {}).get("requested_at") if nightly else None
    action = scheduled_action(event, control.mode() if nightly else None, last_manual_on=last_on)
    if action in ("skip-stale", "skip-keep-on", "skip-new-manual-session"):
        return {"result": action}
    if action == "status":
        return control.status()
    if action == "capture":
        manifest = control.capture()
        return {"result": "captured", "services": manifest["services"], "dns_records": len(manifest["dns"])}
    if action == "validate":
        manifest = control.validate(control.read("restore.json"))
        control.validate_requests(manifest)
        return {"result": "valid", "listeners": len(manifest["listeners"])}
    if action not in ("on", "off"):
        raise ValueError("Use on, off, status, capture, validate, or scheduled-off")
    try:
        return control.on() if action == "on" else control.off(nightly=event.get("action") == "scheduled-off")
    except Exception as exc:
        control.set_state("error-" + action, error=str(exc))
        raise
