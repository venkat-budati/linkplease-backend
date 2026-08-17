import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, create_all, get_session
from app.models import DMJob, JobStatus, utcnow
from app.schemas import RuleCreate, RuleResponse, StatsResponse, WebhookPayload
from app.security import signature_diagnostics, verify_signature
from app.services import create_rule, get_stats, persist_and_process_event
from app.worker import DMWorker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()
    with SessionLocal() as session:
        session.execute(
            update(DMJob)
            .where(DMJob.status == JobStatus.sending)
            .values(status=JobStatus.retry, next_attempt_at=utcnow(), last_error="recovered after restart")
        )
        session.commit()

    worker = None
    task = None
    settings = get_settings()
    if settings.worker_enabled:
        worker = DMWorker(settings)
        task = asyncio.create_task(worker.run())
    try:
        yield
    finally:
        if worker is not None:
            worker.stop()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="LinkPlease Assignment", lifespan=lifespan)


@app.post("/rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
def post_rules(rule_in: RuleCreate, session: Session = Depends(get_session)) -> RuleResponse:
    rule = create_rule(session, rule_in.keyword, rule_in.dm_message)
    return RuleResponse(rule_id=rule.id, keyword=rule.keyword, dm_message=rule.dm_message)


@app.post("/webhook")
async def post_webhook(
    request: Request,
    x_pseudogram_signature: str | None = Header(default=None, alias="X-PseudoGram-Signature"),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    raw_body = await request.body()
    settings = get_settings()
    if not verify_signature(raw_body, x_pseudogram_signature, settings.pseudogram_api_key):
        diagnostics = signature_diagnostics(raw_body, x_pseudogram_signature, settings.pseudogram_api_key)
        logger.warning(
            "webhook signature rejected api_key_present=%s api_key_length=%s signature_present=%s "
            "signature_length=%s expected_signature_length=%s signature_valid=%s",
            diagnostics["api_key_present"],
            diagnostics["api_key_length"],
            diagnostics["signature_present"],
            diagnostics["signature_length"],
            diagnostics["expected_signature_length"],
            diagnostics["signature_valid"],
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")

    payload = WebhookPayload.model_validate_json(raw_body)
    persist_and_process_event(session, payload)
    return {"status": "ok"}


@app.get("/stats", response_model=StatsResponse)
def stats(session: Session = Depends(get_session)) -> StatsResponse:
    return StatsResponse(**get_stats(session))
