import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.audit import audit, verify_chain
from app.db.models import AuditLog, Client, KnowledgeDoc
from app.db.session import scoped_session


def make_clients():
    with scoped_session(owner=True) as s:
        a, b = Client(name="Acme Gym"), Client(name="Bella Salon")
        s.add_all([a, b])
        s.flush()
        s.add_all([
            KnowledgeDoc(client_id=a.id, title="Acme brand voice", content="Energetic", created_by="t"),
            KnowledgeDoc(client_id=b.id, title="Bella brand voice", content="Calm", created_by="t"),
            KnowledgeDoc(client_id=None, title="Agency playbook", content="Cite evidence", created_by="t"),
        ])
        return a.id, b.id


def titles(session):
    return sorted(session.execute(select(KnowledgeDoc.title)).scalars())


def test_client_scope_only_sees_own_and_global_rows():
    a, b = make_clients()
    with scoped_session(client_id=a) as s:
        assert titles(s) == ["Acme brand voice", "Agency playbook"]
        assert [c.name for c in s.execute(select(Client)).scalars()] == ["Acme Gym"]
    with scoped_session() as s:  # no scope: global rows only (fail closed)
        assert titles(s) == ["Agency playbook"]
    with scoped_session(owner=True) as s:
        assert len(titles(s)) == 3


def test_scope_survives_commits_within_a_session():
    a, _ = make_clients()
    from app.db.session import make_session

    s = make_session(client_id=a)
    try:
        assert len(titles(s)) == 2
        s.commit()
        assert len(titles(s)) == 2
    finally:
        s.close()


def test_cannot_write_into_another_clients_data():
    a, b = make_clients()
    with pytest.raises(DBAPIError):
        with scoped_session(client_id=a) as s:
            s.add(KnowledgeDoc(client_id=b, title="sneaky", content="x", created_by="t"))
            s.flush()


def test_audit_log_is_append_only_and_chained():
    with scoped_session(owner=True) as s:
        for i in range(3):
            audit(s, actor_type="system", actor="test", action="test.event", data={"i": i, "api_key": "sk-123"})
    with scoped_session(owner=True) as s:
        assert verify_chain(s)["valid"] is True
        row = s.execute(select(AuditLog).order_by(AuditLog.id.desc())).scalars().first()
        assert row.data["api_key"] == "[REDACTED]"
    with pytest.raises(DBAPIError, match="append-only"):
        with scoped_session(owner=True) as s:
            s.execute(text("UPDATE audit_log SET actor = 'mallory'"))
    with pytest.raises(DBAPIError, match="append-only"):
        with scoped_session(owner=True) as s:
            s.execute(text("DELETE FROM audit_log"))
