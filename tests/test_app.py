import hmac
import json
from pathlib import Path
from uuid import uuid4
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from app import database
from app.config import get_settings
from app.models import Base, DMJob, JobStatus
from app.services import claim_due_send_job, get_stats, mark_accepted, mark_failed_or_retry, mark_permanent_failed


def signed_headers(body: bytes, secret: str = "test-secret") -> dict[str, str]:
    digest = hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    return {"X-PseudoGram-Signature": f"sha256={digest}"}


@pytest.fixture()
def client(monkeypatch):
    db_dir = Path("test-dbs")
    db_dir.mkdir(exist_ok=True)
    db_url = f"sqlite:///{db_dir / f'{uuid4()}.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("PSEUDOGRAM_API_KEY", "test-secret")
    monkeypatch.setenv("WORKER_ENABLED", "false")
    get_settings.cache_clear()

    engine = database.make_engine(db_url)
    database.engine = engine
    database.SessionLocal.configure(bind=engine)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def webhook_payload(event_id="evt_1", text="Can I get the PRICE?", user_id="usr_1", event_type="comment.created"):
    return {
        "event_id": event_id,
        "event_type": event_type,
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {
            "comment_id": "cmt_1",
            "post_id": "post_1",
            "text": text,
            "created_at": "2026-08-10T09:14:21.900Z",
            "from": {"user_id": user_id, "username": "old.name"},
        },
    }


def test_create_rule_and_case_insensitive_substring_match(client):
    response = client.post("/rules", json={"keyword": "PRICE", "dm_message": "price list"})
    assert response.status_code == 201
    assert response.json()["keyword"] == "PRICE"

    body = json.dumps(webhook_payload(text="price please")).encode("utf-8")
    response = client.post("/webhook", content=body, headers=signed_headers(body))
    assert response.status_code == 200

    with database.SessionLocal() as session:
        job = session.query(DMJob).one()
        assert job.user_id == "usr_1"
        assert job.message == "price list"


def test_invalid_signature_is_rejected(client):
    body = json.dumps(webhook_payload()).encode("utf-8")
    response = client.post("/webhook", content=body, headers={"X-PseudoGram-Signature": "sha256=bad"})
    assert response.status_code == 401


def test_duplicate_event_and_duplicate_user_rule_are_blocked(client):
    client.post("/rules", json={"keyword": "price", "dm_message": "price list"})
    body = json.dumps(webhook_payload(event_id="evt_dup")).encode("utf-8")

    assert client.post("/webhook", content=body, headers=signed_headers(body)).status_code == 200
    assert client.post("/webhook", content=body, headers=signed_headers(body)).status_code == 200

    second = webhook_payload(event_id="evt_2", user_id="usr_1")
    second["data"]["comment_id"] = "cmt_2"
    second_body = json.dumps(second).encode("utf-8")
    assert client.post("/webhook", content=second_body, headers=signed_headers(second_body)).status_code == 200

    stats = client.get("/stats").json()
    assert stats["queued"] == 1
    assert stats["duplicates_blocked"] == 2


def test_comment_deleted_cancels_unsent_job_and_blocks_out_of_order_create(client):
    client.post("/rules", json={"keyword": "price", "dm_message": "price list"})
    created = json.dumps(webhook_payload(event_id="evt_created")).encode("utf-8")
    client.post("/webhook", content=created, headers=signed_headers(created))

    deleted_payload = webhook_payload(event_id="evt_deleted", event_type="comment.deleted")
    deleted_payload["data"] = {"comment_id": "cmt_1"}
    deleted = json.dumps(deleted_payload).encode("utf-8")
    client.post("/webhook", content=deleted, headers=signed_headers(deleted))

    late = webhook_payload(event_id="evt_late", user_id="usr_2")
    late["data"]["comment_id"] = "cmt_1"
    late_body = json.dumps(late).encode("utf-8")
    client.post("/webhook", content=late_body, headers=signed_headers(late_body))

    with database.SessionLocal() as session:
        jobs = session.query(DMJob).all()
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.canceled


def test_worker_state_transitions_and_stats(client):
    client.post("/rules", json={"keyword": "price", "dm_message": "price list"})
    body = json.dumps(webhook_payload()).encode("utf-8")
    client.post("/webhook", content=body, headers=signed_headers(body))

    with database.SessionLocal() as session:
        job = claim_due_send_job(session)
        assert job is not None
        assert job.status == JobStatus.sending
        mark_accepted(session, job.id, "dm_1")
        assert get_stats(session)["queued"] == 1
        mark_permanent_failed(session, job.id, "bad request")
        stats = get_stats(session)
        assert stats["failed"] == 1
        assert stats["queued"] == 0


def test_500_retry_and_429_retry_after(client):
    client.post("/rules", json={"keyword": "price", "dm_message": "price list"})
    body = json.dumps(webhook_payload()).encode("utf-8")
    client.post("/webhook", content=body, headers=signed_headers(body))

    with database.SessionLocal() as session:
        job = claim_due_send_job(session)
        mark_failed_or_retry(session, job.id, get_settings(), "server_error:500")
        retry_job = session.get(DMJob, job.id)
        assert retry_job.status == JobStatus.retry

        retry_job.status = JobStatus.sending
        retry_job.attempts = 1
        session.commit()
        mark_failed_or_retry(session, job.id, get_settings(), "rate_limited", retry_after=33)
        limited_job = session.get(DMJob, job.id)
        assert limited_job.status == JobStatus.retry
        assert limited_job.last_error == "rate_limited"


def test_500_event_burst_is_absorbed_into_durable_queue(client):
    client.post("/rules", json={"keyword": "price", "dm_message": "price list"})

    for index in range(500):
        payload = webhook_payload(event_id=f"evt_{index}", user_id=f"usr_{index}")
        payload["data"]["comment_id"] = f"cmt_{index}"
        body = json.dumps(payload).encode("utf-8")
        response = client.post("/webhook", content=body, headers=signed_headers(body))
        assert response.status_code == 200

    assert client.get("/stats").json()["queued"] == 500
