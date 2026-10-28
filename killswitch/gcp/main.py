"""GCP kill switch: Cloud Billing budget -> Pub/Sub -> this Cloud Function.

When reported cost >= budget amount, it detaches the billing account from the project,
which stops all paid services (Google's documented "cap" pattern). This is destructive:
resources may be deleted by Google after billing is disabled. DRY_RUN defaults to "true".
"""
from __future__ import annotations

import base64
import json
import logging
import os

log = logging.getLogger(__name__)


def _billing_client():
    from google.cloud import billing_v1  # imported lazily so tests don't need the SDK
    return billing_v1.CloudBillingClient()


def should_cut(data: dict) -> bool:
    threshold = float(os.environ.get("CUT_AT_FRACTION", "1.0"))
    return float(data.get("costAmount", 0)) >= float(data.get("budgetAmount", 0)) * threshold > 0


def stop_billing(event, context=None, client=None) -> dict:
    data = json.loads(base64.b64decode(event["data"]).decode())
    project = os.environ["TARGET_PROJECT_ID"]
    dry = os.environ.get("DRY_RUN", "true").lower() != "false"
    res = {"project": project, "dry_run": dry, "cost": data.get("costAmount"),
           "budget": data.get("budgetAmount"), "action": "none"}
    if not should_cut(data):
        return res
    client = client or _billing_client()
    name = f"projects/{project}"
    info = client.get_project_billing_info(name=name)
    if not info.billing_enabled:
        res["action"] = "already_disabled"
        return res
    res["action"] = "would_disable_billing" if dry else "disabled_billing"
    if not dry:
        client.update_project_billing_info(name=name, project_billing_info={"billing_account_name": ""})
    log.warning(json.dumps(res))
    return res


def stop_billing_cloudevent(cloud_event):
    """Entry point for Cloud Functions gen2 (Pub/Sub CloudEvent)."""
    return stop_billing({"data": cloud_event.data["message"]["data"]})
