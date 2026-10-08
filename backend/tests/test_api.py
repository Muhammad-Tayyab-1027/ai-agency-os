import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import User
from app.db.session import scoped_session
from app.main import app
from app.workers.worker import run_once

H = {"x-requested-with": "agency-os"}
PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(manager):
    with scoped_session(owner=True) as s:
        s.add(User(email="owner@example.com", password_hash=hash_password(PASSWORD), role="owner"))
        s.add(User(email="viewer@example.com", password_hash=hash_password(PASSWORD), role="viewer"))
    return TestClient(app)


def login(c, email="owner@example.com"):
    r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers=H)
    assert r.status_code == 200, r.text


def test_requires_login_and_csrf_header(client):
    assert client.get("/api/overview").status_code == 401
    r = client.post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert r.status_code == 403  # no CSRF header
    assert client.post("/api/auth/login", json={"email": "owner@example.com", "password": "nope"},
                       headers=H).status_code == 401
    login(client)
    assert client.get("/api/overview").status_code == 200


def test_viewer_cannot_change_anything(client):
    login(client, "viewer@example.com")
    assert client.get("/api/agents").status_code == 200
    r = client.post("/api/goals", json={"title": "x goal", "target_revenue": 100}, headers=H)
    assert r.status_code == 403


def test_goal_to_approval_through_api(client):
    login(client)
    r = client.post("/api/goals", json={"title": "$5k/month", "target_revenue": 5000,
                                        "priority_services": ["website"]}, headers=H)
    assert r.status_code == 200, r.text
    while run_once("api-test"):
        pass
    pending = client.get("/api/approvals").json()
    assert len(pending) == 2
    r = client.post(f"/api/approvals/{pending[0]['id']}/approve", json={}, headers=H)
    assert r.json()["status"] == "executed"
    r = client.post(f"/api/approvals/{pending[1]['id']}/reject", json={"note": "Not yet"}, headers=H)
    assert r.json()["status"] == "rejected"
    keys = {a["key"] for a in client.get("/api/agents").json()}
    assert keys == {"manager", pending[0]["payload"]["role"]}
    chat = client.get("/api/chat").json()
    assert any(m["role"] == "manager" for m in chat)


def test_chat_and_settings(client):
    login(client)
    assert client.post("/api/chat", json={"message": "What's the plan?"}, headers=H).status_code == 200
    run_once("api-test")
    assert [m["role"] for m in client.get("/api/chat").json()] == ["owner", "manager"]
    r = client.put("/api/settings", json={"values": {"paused": True, "max_agents": 5}}, headers=H)
    assert r.json()["paused"] is True and r.json()["max_agents"] == 5
    assert client.put("/api/settings", json={"values": {"max_agents": 999}}, headers=H).status_code == 422
    assert client.get("/api/audit/verify").json()["valid"] is True


def test_suppression_marks_leads_opted_out(client):
    login(client)
    from app.db.models import Lead

    with scoped_session(owner=True) as s:
        s.add(Lead(business_name="Iron Gym", email="hello@irongym.com", source="test"))
    assert client.post("/api/suppression", json={"value": "@irongym.com"}, headers=H).status_code == 200
    with scoped_session(owner=True) as s:
        assert s.execute(select(Lead.opted_out)).scalar_one() is True
