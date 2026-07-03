"""Phase K: REST end-to-end on the dryrun adapter — start → plan gate → decision
→ merge gate → decision → succeeded. Plus WS snapshot, validation, and recovery."""
import time

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.executor_adapters import reset_adapters
from src.sandbox.registry import reset_workspaces
from tests.conftest import FIXTURE_REPO


@pytest.fixture
def client(tmp_path):
    reset_workspaces()
    reset_adapters()
    app = create_app(checkpoint_db=str(tmp_path / "api-ckpt.sqlite"),
                     runs_root=str(tmp_path / "runs"),
                     target_repo=str(FIXTURE_REPO))
    with TestClient(app) as c:
        yield c


def wait_for(fn, timeout=60.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(interval)
    raise AssertionError("timed out waiting for condition")


def pending_gate(client, tid, gate):
    def check():
        r = client.get(f"/runs/{tid}/pending")
        if r.status_code != 200:  # first checkpoint may not have landed yet
            return None
        p = r.json()["pending"]
        return p if p and p.get("gate") == gate else None
    return wait_for(check)


def wait_status(client, tid, *statuses):
    def check():
        r = client.get(f"/runs/{tid}/state")
        if r.status_code != 200:
            return None
        s = r.json()
        return s if s["status"] in statuses else None
    return wait_for(check)


def test_full_run_via_rest(client):
    r = client.post("/runs", json={"tenant_id": "tenant-a"})
    assert r.status_code == 201
    tid = r.json()["thread_id"]

    plan_payload = pending_gate(client, tid, "plan")
    assert [s["file"] for s in plan_payload["plan"]] == ["app/db.py", "app/api.py"]

    state = client.get(f"/runs/{tid}/state").json()
    assert state["status"] == "awaiting_human"
    assert state["token_usage"]["planner"] == 200

    r = client.post(f"/runs/{tid}/decision",
                    json={"action": "approve", "actor": "human:curl"})
    assert r.status_code == 200

    merge_payload = pending_gate(client, tid, "merge")
    assert "UserCreate" in merge_payload["diff"]

    client.post(f"/runs/{tid}/decision", json={"action": "approve", "actor": "human:curl"})
    final = wait_status(client, tid, "succeeded")
    assert final["completed_steps"] == [0, 1]
    assert final["has_final_diff"] is True
    assert [d["actor"] for d in final["approval_history"]] == ["human:curl", "human:curl"]

    diff = client.get(f"/runs/{tid}/diff").json()["diff"]
    assert "UserCreate" in diff and "async def get_user" in diff

    trace = client.get(f"/runs/{tid}/trace").json()
    assert tid in trace["url"] and "16686" in trace["url"]

    runs = client.get("/runs").json()
    assert any(x["thread_id"] == tid and x["status"] == "succeeded" for x in runs)


def test_auto_approve_run_needs_no_decisions(client):
    tid = client.post("/runs", json={"tenant_id": "tenant-b", "auto_approve": True}
                      ).json()["thread_id"]
    final = wait_status(client, tid, "succeeded", "aborted")
    assert final["status"] == "succeeded"
    assert [d["actor"] for d in final["approval_history"]] == ["policy:auto_approve"] * 2


def test_decision_validation(client):
    tid = client.post("/runs", json={"tenant_id": "tenant-c"}).json()["thread_id"]
    pending_gate(client, tid, "plan")
    # wrong action for the plan gate
    r = client.post(f"/runs/{tid}/decision", json={"action": "retry"})
    assert r.status_code == 422
    # unknown run
    assert client.get("/runs/nope/state").status_code == 404
    assert client.post("/runs/nope/decision", json={"action": "approve"}).status_code == 404
    # pi without key is refused up front
    r = client.post("/runs", json={"tenant_id": "t", "executor_adapter": "pi"})
    assert r.status_code == 400


def test_ws_snapshot_and_recovery_endpoint(client):
    tid = client.post("/runs", json={"tenant_id": "tenant-d"}).json()["thread_id"]
    pending_gate(client, tid, "plan")

    with client.websocket_connect(f"/ws/runs/{tid}") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "state"
        assert snapshot["state"]["thread_id"] == tid
        assert snapshot["pending"]["gate"] == "plan"

    # parked at a gate: recovery resume refuses politely, decision is the way
    r = client.post(f"/runs/{tid}/resume")
    assert r.json()["resumed"] is False

    client.post(f"/runs/{tid}/decision", json={"action": "reject", "reason": "just testing"})
    final = wait_status(client, tid, "aborted")
    assert final["failure_reason"] == "just testing"
    # terminal run: recovery resume now 409s
    assert client.post(f"/runs/{tid}/resume").status_code == 409
