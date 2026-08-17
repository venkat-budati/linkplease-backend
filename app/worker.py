import asyncio
import logging

import httpx

from app.config import Settings
from app.database import SessionLocal
from app.models import JobStatus
from app.services import (
    claim_due_reconcile_job,
    claim_due_send_job,
    mark_accepted,
    mark_delivered,
    mark_failed_or_retry,
    mark_permanent_failed,
    reserve_rate_limit_slot,
    retry_after_failed_delivery,
)

logger = logging.getLogger(__name__)


class DMWorker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            did_work = await self._send_one_due_job()
            reconciled = await self._reconcile_one_due_job()
            if not did_work and not reconciled:
                await asyncio.sleep(self.settings.worker_poll_seconds)

    async def _send_one_due_job(self) -> bool:
        with SessionLocal() as session:
            job = claim_due_send_job(session)
        if job is None:
            return False

        with SessionLocal() as session:
            wait_seconds = reserve_rate_limit_slot(session, self.settings)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
            with SessionLocal() as session:
                wait_seconds = reserve_rate_limit_slot(session, self.settings)
            if wait_seconds > 0:
                with SessionLocal() as session:
                    mark_failed_or_retry(session, job.id, self.settings, "rate_limiter_busy", retry_after=int(wait_seconds))
                return True

        payload = {
            "recipient_user_id": job.user_id,
            "message": job.message,
            "comment_id": job.comment_id,
        }
        headers = {
            "X-API-Key": self.settings.pseudogram_api_key,
            "Idempotency-Key": f"dm-job:{job.id}",
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.pseudogram_base_url}/v1/dm/send",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            logger.warning("dm send network error job_id=%s attempt=%s error=%s", job.id, job.attempts, exc)
            with SessionLocal() as session:
                mark_failed_or_retry(session, job.id, self.settings, f"network_error:{exc.__class__.__name__}")
            return True

        with SessionLocal() as session:
            if response.status_code == 202:
                dm_id = response.json().get("dm_id")
                if dm_id:
                    mark_accepted(session, job.id, dm_id)
                else:
                    mark_failed_or_retry(session, job.id, self.settings, "accepted_without_dm_id")
            elif response.status_code == 429:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                mark_failed_or_retry(session, job.id, self.settings, "rate_limited", retry_after=retry_after)
            elif response.status_code >= 500:
                mark_failed_or_retry(session, job.id, self.settings, f"server_error:{response.status_code}")
            elif response.status_code == 400:
                mark_permanent_failed(session, job.id, f"permanent_400:{response.text}")
            else:
                mark_failed_or_retry(session, job.id, self.settings, f"unexpected_status:{response.status_code}")
        return True

    async def _reconcile_one_due_job(self) -> bool:
        with SessionLocal() as session:
            job = claim_due_reconcile_job(session, self.settings)
        if job is None or job.dm_id is None:
            return False

        headers = {"X-API-Key": self.settings.pseudogram_api_key}
        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds) as client:
                response = await client.get(f"{self.settings.pseudogram_base_url}/v1/dm/{job.dm_id}", headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("dm reconcile network error job_id=%s dm_id=%s error=%s", job.id, job.dm_id, exc)
            return True

        if response.status_code != 200:
            logger.warning("dm reconcile unexpected status job_id=%s dm_id=%s status=%s", job.id, job.dm_id, response.status_code)
            return True

        status = response.json().get("status")
        with SessionLocal() as session:
            if status == "delivered":
                mark_delivered(session, job.id)
            elif status == "failed":
                retry_after_failed_delivery(session, job.id, self.settings)
            elif status == "queued":
                mark_accepted(session, job.id, job.dm_id)
        return True


def _parse_retry_after(value: str | None) -> int:
    if value is None:
        return 60
    try:
        return max(int(value), 1)
    except ValueError:
        return 60
