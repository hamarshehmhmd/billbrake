"""AWS kill switch: AWS Budgets -> SNS -> this Lambda.

When the budget alert fires it can:
  * stop EC2 instances (optionally only those carrying a tag),
  * set reserved concurrency 0 on listed Lambda functions (stops them running),
  * attach a deny-all managed policy to listed IAM roles/users.

DRY_RUN defaults to "true": it logs what it would do and changes nothing.
Configuration via environment variables (see terraform/aws).
"""
from __future__ import annotations

import json
import logging
import os

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)


def _env_list(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def _dry() -> bool:
    return os.environ.get("DRY_RUN", "true").lower() != "false"


def stop_ec2(ec2, tag_key: str | None, dry: bool) -> list[str]:
    filters = [{"Name": "instance-state-name", "Values": ["pending", "running"]}]
    if tag_key:
        filters.append({"Name": "tag-key", "Values": [tag_key]})
    ids = []
    for page in ec2.get_paginator("describe_instances").paginate(Filters=filters):
        for r in page["Reservations"]:
            ids += [i["InstanceId"] for i in r["Instances"]]
    if ids and not dry:
        ec2.stop_instances(InstanceIds=ids)
    return ids


def throttle_lambdas(lam, names: list[str], dry: bool) -> list[str]:
    for n in names:
        if not dry:
            lam.put_function_concurrency(FunctionName=n, ReservedConcurrentExecutions=0)
    return names


def deny_principals(iam, policy_arn: str, roles: list[str], users: list[str], dry: bool) -> list[str]:
    done = []
    for r in roles:
        if not dry:
            iam.attach_role_policy(RoleName=r, PolicyArn=policy_arn)
        done.append(f"role/{r}")
    for u in users:
        if not dry:
            iam.attach_user_policy(UserName=u, PolicyArn=policy_arn)
        done.append(f"user/{u}")
    return done


def handler(event, context=None, clients=None):
    clients = clients or {}
    dry = _dry()
    msgs = [rec.get("Sns", {}).get("Message", "") for rec in event.get("Records", [])]
    log.info("budget alert received (dry_run=%s): %s", dry, msgs)
    result = {"dry_run": dry, "ec2_stopped": [], "lambdas_throttled": [], "principals_denied": []}
    if os.environ.get("STOP_EC2", "false").lower() == "true":
        ec2 = clients.get("ec2") or boto3.client("ec2")
        result["ec2_stopped"] = stop_ec2(ec2, os.environ.get("EC2_TAG_KEY") or None, dry)
    fns = _env_list("LAMBDA_FUNCTIONS")
    if fns:
        lam = clients.get("lambda") or boto3.client("lambda")
        result["lambdas_throttled"] = throttle_lambdas(lam, fns, dry)
    policy = os.environ.get("DENY_POLICY_ARN")
    roles, users = _env_list("DENY_ROLES"), _env_list("DENY_USERS")
    if policy and (roles or users):
        iam = clients.get("iam") or boto3.client("iam")
        result["principals_denied"] = deny_principals(iam, policy, roles, users, dry)
    log.info(json.dumps(result))
    return result
