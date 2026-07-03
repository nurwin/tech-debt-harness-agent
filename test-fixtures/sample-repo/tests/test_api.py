"""
These tests define the CONTRACT the refactor must satisfy.

Run against the repo as shipped, several of these FAIL — that is the
point. The harness's verification loop drives edits until they all pass.

They deliberately do NOT assert *how* the fix is implemented (sync vs
async), only the observable behavior, so the harness has room to refactor
db.py to async without breaking the contract.
"""
import pytest
from fastapi.testclient import TestClient

from app.api import app
from app import db


@pytest.fixture(autouse=True)
def _reset():
    db._reset_for_tests()
    yield


client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_seeded_user():
    r = client.get("/users")
    assert r.status_code == 200
    assert any(u["email"] == "ada@example.com" for u in r.json())


def test_get_existing_user():
    r = client.get("/users/1")
    assert r.status_code == 200
    assert r.json()["name"] == "Ada Lovelace"


def test_get_missing_user_returns_404():
    # DEBT: current code returns 200 + null. Refactor must return 404.
    r = client.get("/users/9999")
    assert r.status_code == 404


def test_create_valid_user_returns_201():
    # DEBT: current code returns 200. Refactor must return 201.
    r = client.post("/users", json={"name": "Grace Hopper",
                                    "email": "grace@example.com"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Grace Hopper"
    assert "id" in body


def test_create_rejects_blank_name():
    # DEBT: current code accepts blank name. Refactor must return 422.
    r = client.post("/users", json={"name": "", "email": "x@example.com"})
    assert r.status_code == 422


def test_create_rejects_missing_email():
    # DEBT: current code accepts missing email. Refactor must return 422.
    r = client.post("/users", json={"name": "No Email"})
    assert r.status_code == 422


def test_create_rejects_malformed_email():
    # DEBT: current code accepts junk email. Refactor must return 422.
    r = client.post("/users", json={"name": "Bad Email", "email": "not-an-email"})
    assert r.status_code == 422


def test_delete_existing_then_missing():
    created = client.post("/users", json={"name": "Temp",
                                          "email": "temp@example.com"})
    uid = created.json()["id"]
    r1 = client.delete(f"/users/{uid}")
    assert r1.status_code == 200
    assert r1.json()["deleted"] is True
    # deleting again: user no longer exists
    r2 = client.get(f"/users/{uid}")
    assert r2.status_code == 404
