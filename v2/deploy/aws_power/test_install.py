"""Endpoint IAM must authorize both the new tagged endpoint and its existing dependencies."""
import copy

import pytest
from install import endpoint_create_statements


@pytest.fixture
def config():
    return {"VpcEndpointType": "Interface", "VpcId": "vpc-prod", "SubnetIds": ["subnet-prod"],
            "SecurityGroupIds": ["sg-prod"], "ServiceName": "com.amazonaws.ap-south-1.ecr.api",
            "TagSpecifications": [{"ResourceType": "vpc-endpoint", "Tags": [
                {"Key": "Project", "Value": "agentd"}, {"Key": "Environment", "Value": "production"}]}]}


def test_dependencies_are_exact_and_do_not_require_new_endpoint_tags(config):
    deps, endpoint, tagging, _ = endpoint_create_statements("ap-south-1", "123456789012", [config, copy.deepcopy(config)])
    prefix = "arn:aws:ec2:ap-south-1:123456789012:"
    assert set(deps["Resource"]) == {prefix + "vpc/vpc-prod", prefix + "subnet/subnet-prod", prefix + "security-group/sg-prod"}
    assert "Condition" not in deps
    assert endpoint["Resource"] == prefix + "vpc-endpoint/*"
    assert endpoint["Condition"]["StringEquals"] == {
        "aws:RequestTag/Project": "agentd", "aws:RequestTag/Environment": "production",
        "ec2:VpceServiceName": [config["ServiceName"]]}
    assert tagging["Condition"]["StringEquals"]["ec2:CreateAction"] == "CreateVpcEndpoint"


@pytest.mark.parametrize("owner", ["staging", "", "other"])
def test_other_environment_cannot_expand_endpoint_creation_policy(config, owner):
    config["TagSpecifications"][0]["Tags"][1]["Value"] = owner
    with pytest.raises(ValueError, match="owned production"):
        endpoint_create_statements("ap-south-1", "123456789012", [config])


def test_no_private_endpoints_grants_no_creation_permissions():
    assert endpoint_create_statements("ap-south-1", "123456789012", []) == []
