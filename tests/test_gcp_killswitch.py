import base64
import json
from types import SimpleNamespace

from killswitch.gcp.main import stop_billing


class FakeBilling:
    def __init__(self, enabled=True):
        self.enabled, self.updates = enabled, []

    def get_project_billing_info(self, name):
        return SimpleNamespace(billing_enabled=self.enabled)

    def update_project_billing_info(self, name, project_billing_info):
        self.updates.append((name, project_billing_info))


def ev(cost, budget):
    return {"data": base64.b64encode(json.dumps({"costAmount": cost, "budgetAmount": budget}).encode())}


def test_under_budget_noop(monkeypatch):
    monkeypatch.setenv("TARGET_PROJECT_ID", "p1")
    c = FakeBilling()
    assert stop_billing(ev(5, 10), client=c)["action"] == "none" and not c.updates


def test_dry_run_default(monkeypatch):
    monkeypatch.setenv("TARGET_PROJECT_ID", "p1")
    monkeypatch.delenv("DRY_RUN", raising=False)
    c = FakeBilling()
    assert stop_billing(ev(12, 10), client=c)["action"] == "would_disable_billing" and not c.updates


def test_live_disables(monkeypatch):
    monkeypatch.setenv("TARGET_PROJECT_ID", "p1")
    monkeypatch.setenv("DRY_RUN", "false")
    c = FakeBilling()
    assert stop_billing(ev(10, 10), client=c)["action"] == "disabled_billing"
    assert c.updates == [("projects/p1", {"billing_account_name": ""})]


def test_already_disabled(monkeypatch):
    monkeypatch.setenv("TARGET_PROJECT_ID", "p1")
    monkeypatch.setenv("DRY_RUN", "false")
    assert stop_billing(ev(20, 10), client=FakeBilling(False))["action"] == "already_disabled"


def test_cloudevent_entrypoint(monkeypatch):
    import killswitch.gcp.main as m
    monkeypatch.setenv("TARGET_PROJECT_ID", "p1")
    c = FakeBilling()
    monkeypatch.setattr(m, "_billing_client", lambda: c)
    ce = SimpleNamespace(data={"message": {"data": ev(11, 10)["data"]}})
    assert m.stop_billing_cloudevent(ce)["action"] == "would_disable_billing"
