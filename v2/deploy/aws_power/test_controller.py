"""Lifecycle invariants: no automatic ON, private recoverable OFF, stable DNS."""
import copy
import datetime as dt
import io
import json
from types import SimpleNamespace

import boto3
import controller
import pytest
from controller import (
    Controller,
    dns_points_to,
    normalized_dns_name,
    restored_record,
    scheduled_action,
)


@pytest.fixture
def control(monkeypatch):
    for key, value in {
        "POWER_REGION": "ap-northeast-1", "POWER_ACCOUNT": "123456789012",
        "POWER_STACK": "agentd-test", "POWER_BUCKET": "private-test-bucket",
        "POWER_MODE_PARAMETER": "/agentd/power/test/mode", "POWER_FUNCTION": "agentd-test-power",
        "POWER_SCHEDULE": "agentd-test-power-off-0200",
    }.items():
        monkeypatch.setenv(key, value)
    return Controller(sleep=lambda _: None)


@pytest.fixture
def manifest():
    return {
        "version": 1, "account": "123456789012", "region": "ap-northeast-1",
        "stack": "agentd-test", "attributes": [],
        "listeners": [{"config": {"DefaultActions": [{"Type": "forward"}]}, "rules": []}],
        "dns": [{"zone": "Z1", "record": {"Name": "app.example.com.", "Type": "A"}}],
        "services": [{"name": "agentd-test-web", "desired": 1}],
        "groups": [{"AutoScalingGroupName": "agentd-test-ecs", "MinSize": 0, "MaxSize": 2, "DesiredCapacity": 1}],
        "providers": [], "schedules": [], "functions": [], "rules": [],
    }


def test_nightly_only_off_and_keep_on_persists():
    now = dt.datetime(2026, 10, 12, 17, 0, tzinfo=dt.UTC)
    event = {"action": "scheduled-off", "scheduled_at": "2026-10-12T17:00:00Z"}
    assert scheduled_action(event, "automatic", now) == "off"
    assert scheduled_action(event, "always-on", now) == "skip-keep-on"
    assert scheduled_action(event, "always-on", now) == "skip-keep-on"
    assert scheduled_action({"action": "off"}, "always-on", now) == "off"
    assert scheduled_action({"action": "on"}, "automatic", now) == "on"


@pytest.mark.parametrize("stamp", ["2026-10-12T16:29:59Z", "2026-10-12T17:01:01Z"])
def test_late_or_future_schedule_cannot_stop_new_manual_session(stamp):
    now = dt.datetime(2026, 10, 12, 17, 0, tzinfo=dt.UTC)
    assert scheduled_action({"action": "scheduled-off", "scheduled_at": stamp}, "automatic", now) == "skip-stale"


@pytest.mark.parametrize("stamp", [None, "2026-10-12T17:00:00"])
def test_scheduled_off_requires_authenticated_scheduler_timestamp(stamp):
    with pytest.raises(ValueError):
        scheduled_action({"action": "scheduled-off", "scheduled_at": stamp}, "automatic")


def test_dns_matching_does_not_select_unrelated_records():
    assert dns_points_to({"Type": "A", "AliasTarget": {"DNSName": "OLD.elb.amazonaws.com."}}, "old.elb.amazonaws.com")
    assert not dns_points_to({"Type": "TXT", "ResourceRecords": [{"Value": "old.elb.amazonaws.com"}]}, "old.elb.amazonaws.com")
    assert not dns_points_to({"Type": "A", "AliasTarget": {"DNSName": "other.elb.amazonaws.com"}}, "old.elb.amazonaws.com")


def test_dns_iam_condition_uses_octal_wildcards():
    assert normalized_dns_name("*.Example.com.") == "\\052.example.com"
    assert normalized_dns_name("\\052.Example.com.") == "\\052.example.com"


def test_dns_restore_preserves_routing_identity_and_hostname():
    record = {"Name": "app.example.com.", "Type": "A", "SetIdentifier": "prod", "Weight": 10,
              "AliasTarget": {"DNSName": "old", "HostedZoneId": "oldzone", "EvaluateTargetHealth": False}}
    result = restored_record(record, {"DNSName": "new", "CanonicalHostedZoneId": "newzone"})
    assert result == {**record, "AliasTarget": {"DNSName": "new", "HostedZoneId": "newzone", "EvaluateTargetHealth": False}}
    assert record["AliasTarget"]["DNSName"] == "old"


@pytest.mark.parametrize("phase", ["off", "stopping", "starting", "error-off", "error-on"])
def test_repeat_off_never_overwrites_restore_counts(control, manifest, phase):
    control.read = lambda key, default=None: {"phase": phase} if key == "state.json" else manifest
    control.lb = lambda: pytest.fail("Partial/off state must use saved manifest")
    assert control.capture()["services"][0]["desired"] == 1


def test_recovery_snapshot_rejects_wrong_stack(control, manifest):
    manifest["stack"] = "some-other-stack"
    with pytest.raises(RuntimeError, match="another AWS stack"):
        control.validate(manifest)


def test_recovery_snapshot_rejects_unrecoverable_auth_secrets(control, manifest):
    manifest["listeners"][0]["rules"] = [{"Actions": [{"Type": "authenticate-oidc"}]}]
    with pytest.raises(RuntimeError, match="secure recovery"):
        control.validate(manifest)


def test_failed_snapshot_verification_blocks_teardown(control):
    calls = []
    control.clients["s3"] = SimpleNamespace(
        put_object=lambda **kwargs: calls.append(kwargs),
        get_object=lambda **kwargs: {"Body": io.BytesIO(b'{"corrupted":true}')},
    )
    with pytest.raises(RuntimeError, match="verify"):
        control.write("restore.json", {"services": [{"desired": 1}]})
    assert len(calls) == 1


def test_sdk_schema_removes_response_only_fields(control):
    control.clients["elbv2"] = boto3.client("elbv2", region_name=control.region,
        aws_access_key_id="testing", aws_secret_access_key="testing")
    value = control.request("elbv2", "CreateListener", {
        "ListenerArn": "old", "LoadBalancerArn": "new", "Port": 443, "Protocol": "HTTPS",
        "Certificates": [{"CertificateArn": "cert", "IsDefault": True}], "DefaultActions": [{"Type": "forward", "TargetGroupArn": "tg"}],
    })
    assert "ListenerArn" not in value
    assert value["Certificates"] == [{"CertificateArn": "cert"}]


def test_keep_on_skips_all_teardown(control, monkeypatch):
    control.mode = lambda: "always-on"
    control.read = lambda *args: {}
    control.capture = lambda: pytest.fail("Keep-on must not change compute")
    monkeypatch.setattr(controller, "Controller", lambda: control)
    assert controller.handler({"action": "scheduled-off", "scheduled_at": dt.datetime.now(dt.UTC).isoformat()}, None) == {"result": "skip-keep-on"}


def test_manual_on_only_and_manual_off_ignores_keep_on(control, monkeypatch):
    calls = []
    control.mode = lambda: pytest.fail("Manual actions must not depend on the override")
    control.on = lambda: calls.append("on") or {"phase": "on"}
    control.off = lambda **kwargs: calls.append(("off", kwargs)) or {"phase": "off"}
    monkeypatch.setattr(controller, "Controller", lambda: control)
    assert controller.handler({"action": "on"}, None)["phase"] == "on"
    assert controller.handler({"action": "off"}, None)["phase"] == "off"
    assert calls == ["on", ("off", {"nightly": False})]


def test_capture_failure_prevents_any_shutdown(control):
    control.capture = lambda: (_ for _ in ()).throw(RuntimeError("snapshot unavailable"))
    control.set_jobs = lambda *args: pytest.fail("No shutdown without verified snapshot")
    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        control.off()


def test_off_disables_resurrection_before_compute_and_retains_restore(control, manifest):
    calls = []
    original = copy.deepcopy(manifest)
    control.capture = lambda: manifest
    control.set_state = lambda phase, **details: calls.append(phase) or {"phase": phase}
    control.set_jobs = lambda data, enabled: calls.append(("jobs", enabled))
    control.set_providers = lambda data, enabled: calls.append(("scaling", enabled))
    control.services = lambda: [{"serviceName": "agentd-test-web"}]
    control.all = lambda *args, **kwargs: []
    control.clients["ecs"] = SimpleNamespace(update_service=lambda **kwargs: calls.append(("service", kwargs["desiredCount"])))
    control.clients["autoscaling"] = SimpleNamespace(
        describe_auto_scaling_groups=lambda **kwargs: {"AutoScalingGroups": [{"Instances": [{"InstanceId": "i-test", "ProtectedFromScaleIn": True}]}]},
        set_instance_protection=lambda **kwargs: calls.append(("protection", kwargs["ProtectedFromScaleIn"])),
        update_auto_scaling_group=lambda **kwargs: calls.append(("host", kwargs["DesiredCapacity"])),
    )
    control.lb = lambda: {"LoadBalancerArn": "lb-test"}
    control.clients["elbv2"] = SimpleNamespace(delete_load_balancer=lambda **kwargs: calls.append("delete-lb"))
    assert control.off()["phase"] == "off"
    assert calls == ["stopping", ("jobs", False), ("scaling", False), ("service", 0), ("protection", False), ("host", 0), "delete-lb", "off"]
    assert manifest == original


def test_service_autoscaling_cannot_resurrect_stopped_compute(control, manifest):
    target = {"ResourceId": "service/agentd-test/agentd-test-web", "ScalableDimension": "ecs:service:DesiredCount",
              "MinCapacity": 1, "MaxCapacity": 4, "SuspendedState": {"DynamicScalingInSuspended": False,
                  "DynamicScalingOutSuspended": False, "ScheduledScalingSuspended": False}}
    manifest["service_scaling"] = [target]
    calls = []
    control.clients["application-autoscaling"] = SimpleNamespace(register_scalable_target=lambda **kwargs: calls.append(kwargs))
    control.set_service_scaling(manifest, False)
    assert calls[0]["MinCapacity"] == calls[0]["MaxCapacity"] == 0
    assert all(calls[0]["SuspendedState"].values())
    control.set_service_scaling(manifest, True)
    assert calls[1]["MinCapacity"] == 1 and calls[1]["MaxCapacity"] == 4
    assert calls[1]["SuspendedState"] == target["SuspendedState"]


def test_status_explicitly_has_no_automatic_start(control):
    control.mode = lambda: "automatic"
    control.read = lambda *args: {"phase": "off"}
    control.lb = lambda: None
    control.services = lambda: []
    assert json.loads(json.dumps(control.status()))["automatic_start"] is False


def test_on_restores_in_order_and_only_enables_jobs_when_healthy(control, manifest):
    calls = []
    control.read = lambda *args: manifest
    control.write = lambda *args: None
    control.set_state = lambda phase, **details: calls.append(phase) or {"phase": phase}
    control.restore_lb = lambda data: calls.append("restore-lb-and-dns") or {"DNSName": "new-lb"}
    control.set_providers = lambda data, enabled: calls.append(("hosts-scaling", enabled))
    control.set_jobs = lambda data, enabled: calls.append(("jobs", enabled))
    control.set_service_scaling = lambda data, enabled: calls.append(("service-scaling", enabled))
    control.clients["autoscaling"] = SimpleNamespace(update_auto_scaling_group=lambda **kwargs: calls.append(("hosts", kwargs["DesiredCapacity"])))
    control.clients["ecs"] = SimpleNamespace(update_service=lambda **kwargs: calls.append(("service", kwargs["desiredCount"])))
    control.services = lambda: [{"serviceName": "agentd-test-web", "runningCount": 1, "pendingCount": 0,
                                "deployments": [{"rolloutState": "COMPLETED"}]}]
    assert control.on()["phase"] == "on"
    assert calls == ["starting", "restore-lb-and-dns", ("hosts", 1), ("hosts-scaling", True),
                     ("service", 1), ("jobs", True), ("service-scaling", True), "on"]


def test_on_failure_preserves_manifest_and_does_not_enable_jobs(control, manifest):
    original = copy.deepcopy(manifest)
    control.read = lambda *args: manifest
    control.write = lambda *args: None
    control.set_state = lambda *args: None
    control.restore_lb = lambda data: (_ for _ in ()).throw(RuntimeError("TLS/DNS restore failed"))
    control.set_jobs = lambda *args: pytest.fail("Jobs cannot run before restored service health")
    with pytest.raises(RuntimeError, match="restore failed"):
        control.on()
    assert manifest == original


def test_off_waits_for_stopping_tasks_that_disappeared_from_running_list(control, manifest):
    calls = []
    pages = iter([["task-pre-stop"], [], []])
    task_states = iter(["DEACTIVATING", "STOPPED"])
    control.capture = lambda: manifest
    control.set_state = lambda phase, **kwargs: {"phase": phase}
    control.set_jobs = lambda *args: None
    control.set_providers = lambda *args: None
    control.services = lambda: [{"serviceName": "agentd-test-web"}]
    control.all = lambda *args, **kwargs: next(pages)
    def describe(**kwargs):
        state = next(task_states)
        calls.append(state)
        return {"tasks": [{"taskArn": "task-pre-stop", "lastStatus": state, "desiredStatus": "STOPPED"}]}
    control.clients["ecs"] = SimpleNamespace(update_service=lambda **kwargs: None, describe_tasks=describe,
        stop_task=lambda **kwargs: pytest.fail("Do not stop already draining/stopped tasks again"))
    control.clients["autoscaling"] = SimpleNamespace(
        describe_auto_scaling_groups=lambda **kwargs: {"AutoScalingGroups": [{"Instances": []}]},
        update_auto_scaling_group=lambda **kwargs: calls.append("remove-host"))
    control.lb = lambda: None
    assert control.off()["phase"] == "off"
    assert calls == ["DEACTIVATING", "STOPPED", "remove-host"]


def test_long_start_before_2am_still_gets_queued_shutdown():
    event = {"action": "scheduled-off", "scheduled_at": "2026-10-12T17:00:00Z"}
    now = dt.datetime(2026, 10, 12, 17, 10, tzinfo=dt.UTC)
    assert scheduled_action(event, "automatic", now, "2026-10-12T16:59:00Z") == "off"


def test_manual_start_after_2am_invalidates_old_queued_shutdown():
    event = {"action": "scheduled-off", "scheduled_at": "2026-10-12T17:00:00Z"}
    now = dt.datetime(2026, 10, 12, 17, 10, tzinfo=dt.UTC)
    assert scheduled_action(event, "automatic", now, "2026-10-12T17:01:00Z") == "skip-new-manual-session"


def test_retired_staging_cannot_be_started(control):
    control.prefix = "agentd-staging"
    control.read = lambda *args: pytest.fail("Do not restore retired staging")
    with pytest.raises(RuntimeError, match="Staging is retired"):
        control.on()


def test_capacity_provider_updates_wait_for_each_other(control, manifest):
    calls = []
    active = []
    config = {"managedScaling": {"status": "ENABLED"}, "managedTerminationProtection": "ENABLED"}
    manifest["providers"] = [{"name": "first", "config": config}, {"name": "second", "config": config}]
    def update(**kwargs):
        assert not active, "ECS cluster cannot update two provider attachments concurrently"
        active.append(kwargs["name"])
        calls.append(("update", kwargs["name"]))
    def describe(**kwargs):
        name = kwargs["capacityProviders"][0]
        assert active == [name]
        active.clear()
        calls.append(("completed", name))
        return {"capacityProviders": [{"name": name, "updateStatus": "UPDATE_COMPLETE"}]}
    control.request = lambda service, operation, value: value
    control.clients["ecs"] = SimpleNamespace(
        describe_clusters=lambda **kwargs: {"clusters": [{"attachmentsStatus": "UPDATE_COMPLETE"}]},
        update_capacity_provider=update, describe_capacity_providers=describe)
    control.set_providers(manifest, False)
    assert calls == [("update", "first"), ("completed", "first"), ("update", "second"), ("completed", "second")]


@pytest.fixture
def endpoint_manifest(manifest):
    manifest["endpoints"] = [{"id": "vpce-old", "config": {
        "VpcId": "vpc-prod", "ServiceName": "com.amazonaws.ap-south-1.ecr.api",
        "VpcEndpointType": "Interface", "SubnetIds": ["subnet-prod"],
        "SecurityGroupIds": ["sg-prod"], "PrivateDnsEnabled": True,
        "PolicyDocument": '{"Statement":[]}',
        "TagSpecifications": [{"ResourceType": "vpc-endpoint", "Tags": [
            {"Key": "Project", "Value": "agentd"},
            {"Key": "Environment", "Value": "production"},
        ]}],
    }}]
    return manifest


def test_on_recreates_deleted_private_network_before_using_it(control, endpoint_manifest):
    calls = []
    available = {"VpcEndpointId": "vpce-new", "State": "available"}
    def describe(**kwargs):
        return {"VpcEndpoints": [available] if "VpcEndpointIds" in kwargs else []}
    def create(**kwargs):
        calls.append(kwargs)
        return {"VpcEndpoint": available}
    control.clients["ec2"] = SimpleNamespace(describe_vpc_endpoints=describe,
                                              create_vpc_endpoint=create)
    mapping = control.restore_endpoints(endpoint_manifest)
    assert mapping == {"com.amazonaws.ap-south-1.ecr.api": "vpce-new"}
    assert calls == [endpoint_manifest["endpoints"][0]["config"]]


def test_on_reuses_owned_private_endpoint_without_duplicate_cost(control, endpoint_manifest):
    endpoint = {"VpcEndpointId": "vpce-existing", "State": "available",
                "Tags": endpoint_manifest["endpoints"][0]["config"]["TagSpecifications"][0]["Tags"]}
    control.clients["ec2"] = SimpleNamespace(
        describe_vpc_endpoints=lambda **kwargs: {"VpcEndpoints": [endpoint]},
        create_vpc_endpoint=lambda **kwargs: pytest.fail("Already exists"))
    assert control.restore_endpoints(endpoint_manifest) == {
        "com.amazonaws.ap-south-1.ecr.api": "vpce-existing"}


def test_on_refuses_to_adopt_staging_or_unowned_network(control, endpoint_manifest):
    endpoint = {"VpcEndpointId": "vpce-other", "State": "available",
                "Tags": [{"Key": "Project", "Value": "agentd"},
                         {"Key": "Environment", "Value": "staging"}]}
    control.clients["ec2"] = SimpleNamespace(
        describe_vpc_endpoints=lambda **kwargs: {"VpcEndpoints": [endpoint]},
        create_vpc_endpoint=lambda **kwargs: pytest.fail("Do not create duplicates"))
    with pytest.raises(RuntimeError, match="outside production ownership"):
        control.restore_endpoints(endpoint_manifest)


def test_failed_endpoint_creation_does_not_claim_network_is_ready(control, endpoint_manifest):
    failed = {"VpcEndpointId": "vpce-failed", "State": "failed"}
    control.clients["ec2"] = SimpleNamespace(
        describe_vpc_endpoints=lambda **kwargs: {"VpcEndpoints": [failed] if "VpcEndpointIds" in kwargs else []},
        create_vpc_endpoint=lambda **kwargs: {"VpcEndpoint": failed})
    with pytest.raises(RuntimeError, match="did not become ready"):
        control.restore_endpoints(endpoint_manifest)


@pytest.mark.parametrize("operation", ["CreateRule", "ModifyRule"])
@pytest.mark.parametrize("field,config", [
    ("host-header", "HostHeaderConfig"), ("path-pattern", "PathPatternConfig")])
def test_restored_listener_rules_use_one_condition_representation(control, operation, field, config):
    control.clients["elbv2"] = boto3.client("elbv2", region_name=control.region,
        aws_access_key_id="testing", aws_secret_access_key="testing")
    value = control.request("elbv2", operation, {
        "ListenerArn": "listener", "RuleArn": "rule", "Priority": 1,
        "Conditions": [{"Field": field, "Values": ["legacy"], config: {"Values": ["typed"]}}],
        "Actions": [{"Type": "forward", "TargetGroupArn": "target"}],
    })
    assert value["Conditions"] == [{"Field": field, config: {"Values": ["typed"]}}]
