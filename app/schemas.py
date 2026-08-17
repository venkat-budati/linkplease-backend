from datetime import datetime

from pydantic import BaseModel, Field


class RuleCreate(BaseModel):
    keyword: str = Field(min_length=1, max_length=255)
    dm_message: str = Field(min_length=1)


class RuleResponse(BaseModel):
    rule_id: str
    keyword: str
    dm_message: str


class WebhookUser(BaseModel):
    user_id: str | None = None
    username: str | None = None


class WebhookData(BaseModel):
    comment_id: str
    post_id: str | None = None
    text: str | None = None
    created_at: datetime | None = None
    from_: WebhookUser | None = Field(default=None, alias="from")


class WebhookPayload(BaseModel):
    event_id: str
    event_type: str
    sent_at: datetime | None = None
    data: WebhookData


class StatsResponse(BaseModel):
    sent: int
    failed: int
    queued: int
    duplicates_blocked: int
