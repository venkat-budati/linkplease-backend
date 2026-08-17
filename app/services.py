from datetime import datetime, timedelta, timezone
from random import uniform

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ApiRequestLog, DeletedComment, DMJob, Event, EventState, JobStatus, Rule, StatCounter, utcnow
from app.schemas import WebhookPayload


QUEUE_STATUSES = (JobStatus.queued, JobStatus.retry, JobStatus.sending, JobStatus.accepted)


def increment_counter(session: Session, name: str, amount: int = 1) -> None:
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect in {"mysql", "mariadb"}:
        stmt = mysql_insert(StatCounter).values(name=name, value=amount)
        stmt = stmt.on_duplicate_key_update(value=StatCounter.value + amount)
        session.execute(stmt)
        return

    counter = session.get(StatCounter, name)
    if counter is None:
        session.add(StatCounter(name=name, value=amount))
    else:
        counter.value += amount


def create_rule(session: Session, keyword: str, dm_message: str) -> Rule:
    rule = Rule(keyword=keyword.strip(), dm_message=dm_message)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def persist_and_process_event(session: Session, payload: WebhookPayload) -> bool:
    data = payload.data
    user = data.from_
    event = Event(
        event_id=payload.event_id,
        event_type=payload.event_type,
        comment_id=data.comment_id,
        post_id=data.post_id,
        user_id=user.user_id if user else None,
        username=user.username if user else None,
        text=data.text,
        comment_created_at=data.created_at,
        sent_at=payload.sent_at,
        state=EventState.processed,
    )
    session.add(event)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        increment_counter(session, "duplicates_blocked")
        session.commit()
        return False

    if payload.event_type == "comment.created":
        _process_comment_created(session, payload)
    elif payload.event_type == "comment.deleted":
        _process_comment_deleted(session, payload)
    else:
        event.state = EventState.ignored

    session.commit()
    return True


def _process_comment_created(session: Session, payload: WebhookPayload) -> None:
    data = payload.data
    user = data.from_
    if not user or not user.user_id or not data.text:
        return

    if session.get(DeletedComment, data.comment_id) is not None:
        return

    text = data.text.casefold()
    rules = session.scalars(select(Rule)).all()
    for rule in rules:
        if rule.keyword.casefold() not in text:
            continue
        job = DMJob(rule_id=rule.id, user_id=user.user_id, comment_id=data.comment_id, message=rule.dm_message)
        try:
            with session.begin_nested():
                session.add(job)
                session.flush()
        except IntegrityError:
            increment_counter(session, "duplicates_blocked")


def _process_comment_deleted(session: Session, payload: WebhookPayload) -> None:
    comment_id = payload.data.comment_id
    if session.get(DeletedComment, comment_id) is None:
        session.add(DeletedComment(comment_id=comment_id))
    session.execute(
        update(DMJob)
        .where(DMJob.comment_id == comment_id, DMJob.status.in_([JobStatus.queued, JobStatus.retry]))
        .values(status=JobStatus.canceled, last_error="comment deleted before send", updated_at=utcnow())
    )


def claim_due_send_job(session: Session) -> DMJob | None:
    now = utcnow()
    query = (
        select(DMJob)
        .where(DMJob.status.in_([JobStatus.queued, JobStatus.retry]), DMJob.next_attempt_at <= now)
        .order_by(DMJob.next_attempt_at, DMJob.created_at)
        .limit(1)
    )
    if session.bind is not None and session.bind.dialect.name in {"mysql", "mariadb"}:
        query = query.with_for_update(skip_locked=True)
    job = session.scalars(query).first()
    if job is None:
        return None
    job.status = JobStatus.sending
    job.attempts += 1
    job.updated_at = now
    session.commit()
    session.refresh(job)
    return job


def claim_due_reconcile_job(session: Session, settings: Settings) -> DMJob | None:
    threshold = utcnow() - timedelta(seconds=settings.reconcile_after_seconds)
    query = (
        select(DMJob)
        .where(DMJob.status == JobStatus.accepted, DMJob.dm_id.is_not(None), DMJob.updated_at <= threshold)
        .order_by(DMJob.updated_at)
        .limit(1)
    )
    if session.bind is not None and session.bind.dialect.name in {"mysql", "mariadb"}:
        query = query.with_for_update(skip_locked=True)
    job = session.scalars(query).first()
    if job is None:
        return None
    job.updated_at = utcnow()
    session.commit()
    session.refresh(job)
    return job


def mark_accepted(session: Session, job_id: str, dm_id: str) -> None:
    job = session.get(DMJob, job_id)
    if job is None:
        return
    job.status = JobStatus.accepted
    job.dm_id = dm_id
    job.last_error = None
    job.next_attempt_at = utcnow() + timedelta(seconds=5)
    session.commit()


def mark_delivered(session: Session, job_id: str) -> None:
    job = session.get(DMJob, job_id)
    if job is None or job.status == JobStatus.delivered:
        return
    job.status = JobStatus.delivered
    job.last_error = None
    increment_counter(session, "sent")
    session.commit()


def mark_failed_or_retry(session: Session, job_id: str, settings: Settings, error: str, retry_after: int | None = None) -> None:
    job = session.get(DMJob, job_id)
    if job is None:
        return
    if job.attempts >= settings.max_dm_attempts:
        job.status = JobStatus.failed
        job.last_error = error
        increment_counter(session, "failed")
    else:
        delay = retry_after if retry_after is not None else _backoff_seconds(job.attempts)
        job.status = JobStatus.retry
        job.next_attempt_at = utcnow() + timedelta(seconds=delay)
        job.last_error = error
    session.commit()


def mark_permanent_failed(session: Session, job_id: str, error: str) -> None:
    job = session.get(DMJob, job_id)
    if job is None or job.status == JobStatus.failed:
        return
    job.status = JobStatus.failed
    job.last_error = error
    increment_counter(session, "failed")
    session.commit()


def retry_after_failed_delivery(session: Session, job_id: str, settings: Settings) -> None:
    job = session.get(DMJob, job_id)
    if job is None:
        return
    job.dm_id = None
    mark_failed_or_retry(session, job_id, settings, "accepted dm later failed")


def reserve_rate_limit_slot(session: Session, settings: Settings) -> float:
    now = utcnow()
    window_start = now - timedelta(seconds=settings.rate_limit_window_seconds)
    session.execute(delete(ApiRequestLog).where(ApiRequestLog.created_at < window_start))
    recent = session.scalars(select(ApiRequestLog.created_at).order_by(ApiRequestLog.created_at)).all()
    if len(recent) >= settings.rate_limit_requests:
        oldest = recent[0]
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        wait_seconds = (oldest + timedelta(seconds=settings.rate_limit_window_seconds) - now).total_seconds()
        session.rollback()
        return max(wait_seconds, 0.1)
    session.add(ApiRequestLog())
    session.commit()
    return 0.0


def get_stats(session: Session) -> dict[str, int]:
    counters = {row.name: row.value for row in session.scalars(select(StatCounter)).all()}
    queued = session.scalar(select(func.count()).select_from(DMJob).where(DMJob.status.in_(QUEUE_STATUSES))) or 0
    return {
        "sent": counters.get("sent", 0),
        "failed": counters.get("failed", 0),
        "queued": int(queued),
        "duplicates_blocked": counters.get("duplicates_blocked", 0),
    }


def _backoff_seconds(attempts: int) -> int:
    capped = min(60, 2 ** max(attempts, 1))
    return int(capped + uniform(0, 1))
