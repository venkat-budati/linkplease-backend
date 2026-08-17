import enum
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventState(str, enum.Enum):
    processed = "processed"
    ignored = "ignored"


class JobStatus(str, enum.Enum):
    queued = "queued"
    retry = "retry"
    sending = "sending"
    accepted = "accepted"
    delivered = "delivered"
    failed = "failed"
    canceled = "canceled"


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    dm_message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    jobs: Mapped[list["DMJob"]] = relationship(back_populates="rule")


class Event(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    comment_id: Mapped[str | None] = mapped_column(String(128), index=True)
    post_id: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(128), index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str | None] = mapped_column(Text)
    comment_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    state: Mapped[EventState] = mapped_column(Enum(EventState), default=EventState.processed, nullable=False)


class DeletedComment(Base):
    __tablename__ = "deleted_comments"

    comment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DMJob(Base):
    __tablename__ = "dm_jobs"
    __table_args__ = (UniqueConstraint("rule_id", "user_id", name="uq_dm_jobs_rule_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    rule_id: Mapped[str] = mapped_column(ForeignKey("rules.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    comment_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, index=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dm_id: Mapped[str | None] = mapped_column(String(128), index=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    rule: Mapped[Rule] = relationship(back_populates="jobs")


class StatCounter(Base):
    __tablename__ = "stat_counters"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ApiRequestLog(Base):
    __tablename__ = "api_request_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
