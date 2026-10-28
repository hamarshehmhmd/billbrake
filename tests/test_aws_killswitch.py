import boto3
import pytest
from botocore.stub import Stubber

from killswitch.aws import handler as h

EVENT = {"Records": [{"Sns": {"Message": "AWS Budget Notification: actual > 100%"}}]}


@pytest.fixture
def clients():
    kw = {"region_name": "us-east-1", "aws_access_key_id": "x", "aws_secret_access_key": "x"}
    return {"ec2": boto3.client("ec2", **kw), "lambda": boto3.client("lambda", **kw), "iam": boto3.client("iam", **kw)}


def desc():
    return {"Reservations": [{"Instances": [{"InstanceId": "i-1"}, {"InstanceId": "i-2"}]}]}


def test_dry_run_changes_nothing(monkeypatch, clients):
    monkeypatch.setenv("STOP_EC2", "true")
    monkeypatch.setenv("LAMBDA_FUNCTIONS", "fn-a")
    monkeypatch.delenv("DRY_RUN", raising=False)
    with Stubber(clients["ec2"]) as s, Stubber(clients["lambda"]):
        s.add_response("describe_instances", desc())
        out = h.handler(EVENT, clients=clients)  # any stop/put call would raise: no stubbed response
        s.assert_no_pending_responses()
    assert out == {"dry_run": True, "ec2_stopped": ["i-1", "i-2"], "lambdas_throttled": ["fn-a"],
                   "principals_denied": []}


def test_live_run_calls_apis(monkeypatch, clients):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("STOP_EC2", "true")
    monkeypatch.setenv("EC2_TAG_KEY", "billbrake")
    monkeypatch.setenv("LAMBDA_FUNCTIONS", "fn-a")
    monkeypatch.setenv("DENY_POLICY_ARN", "arn:aws:iam::123456789012:policy/BillbrakeDenyAll")
    monkeypatch.setenv("DENY_ROLES", "app-role")
    monkeypatch.setenv("DENY_USERS", "ci-user")
    with Stubber(clients["ec2"]) as e, Stubber(clients["lambda"]) as lam, Stubber(clients["iam"]) as iam:
        e.add_response("describe_instances", desc(), {"Filters": [
            {"Name": "instance-state-name", "Values": ["pending", "running"]},
            {"Name": "tag-key", "Values": ["billbrake"]}]})
        e.add_response("stop_instances", {"StoppingInstances": []}, {"InstanceIds": ["i-1", "i-2"]})
        lam.add_response("put_function_concurrency", {"ReservedConcurrentExecutions": 0},
                         {"FunctionName": "fn-a", "ReservedConcurrentExecutions": 0})
        arn = "arn:aws:iam::123456789012:policy/BillbrakeDenyAll"
        iam.add_response("attach_role_policy", {}, {"RoleName": "app-role", "PolicyArn": arn})
        iam.add_response("attach_user_policy", {}, {"UserName": "ci-user", "PolicyArn": arn})
        out = h.handler(EVENT, clients=clients)
        for s in (e, lam, iam):
            s.assert_no_pending_responses()
    assert out["principals_denied"] == ["role/app-role", "user/ci-user"]
