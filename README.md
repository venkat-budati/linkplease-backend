# LinkPlease Backend

Production-minded FastAPI implementation for the LinkPlease intern assignment. It ingests PseudoGram comment webhooks, creates durable DM jobs for matching keyword rules, sends DMs through the mock API under rate limits, reconciles accepted DMs, and reports database-backed stats.

## Architecture

Webhook ingestion is separated from external API delivery:

1. `POST /webhook` reads the raw body, verifies `X-PseudoGram-Signature`, persists the event, creates matching DM jobs, and returns quickly.
2. A background worker claims due jobs from the database, throttles outbound `/v1/dm/send` requests, and handles `202`, `429`, `500`, and `400`.
3. Accepted DMs are reconciled with `GET /v1/dm/{dm_id}` until they become `delivered` or `failed`.

The queue is the `dm_jobs` table, not process memory. Restarting the API does not erase pending work.

## API

`POST /rules`

```json
{ "keyword": "PRICE", "dm_message": "Here's the price list: ..." }
```

Returns `201`:

```json
{ "rule_id": "uuid", "keyword": "PRICE", "dm_message": "Here's the price list: ..." }
```

`POST /webhook`

Accepts the assignment webhook payload. The signature is HMAC-SHA256 of the raw body using `PSEUDOGRAM_API_KEY`, formatted as `sha256=<hex>`.

`GET /stats`

```json
{
  "sent": 0,
  "failed": 0,
  "queued": 0,
  "duplicates_blocked": 0
}
```

`sent` is incremented only after reconciliation confirms `delivered`. `queued` includes jobs waiting to send, waiting to retry, currently sending, or accepted but not terminal. `duplicates_blocked` includes duplicate `event_id` deliveries and duplicate user/rule DM attempts blocked by the unique database constraint.

## Database Design

Main tables:

- `rules`: keyword and DM message.
- `events`: all accepted webhook events, with `event_id` as the primary key.
- `dm_jobs`: durable outbound DM queue. `UNIQUE(rule_id, user_id)` prevents sending the same user more than once for the same rule, even under concurrent webhooks.
- `deleted_comments`: tombstones for `comment.deleted`, including out-of-order deletion.
- `stat_counters`: durable counters for delivered, failed, and duplicate-blocked totals.
- `api_request_log`: persisted outbound send timestamps used to stay below 10 sends per rolling 60 seconds.

MySQL 8 is the intended deployment database. SQLite is used only by the automated unit tests as a lightweight local fallback.

## Reliability Choices

- Event idempotency uses the unique `events.event_id`.
- DM idempotency uses `UNIQUE(rule_id, user_id)` plus `Idempotency-Key: dm-job:<job_id>` to the mock API.
- `500` and network errors retry with bounded exponential backoff plus jitter.
- `429` respects `Retry-After`.
- `400` is permanent and is not retried.
- `202` stores `dm_id` but is not counted as sent.
- `comment.deleted` cancels queued/retry jobs for that comment. If the DM is already accepted or delivered, the system records reality because the mock API has no unsend endpoint.
- On startup, any job stuck in `sending` is moved back to retry.

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Create a MySQL database and user before starting the API. Example SQL:

```sql
CREATE DATABASE linkplease CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'linkplease'@'localhost' IDENTIFIED BY 'change-me';
GRANT ALL PRIVILEGES ON linkplease.* TO 'linkplease'@'localhost';
FLUSH PRIVILEGES;
```

Set `DATABASE_URL` in `.env`, then start FastAPI:

```bash
uvicorn app.main:app --reload
```

The durable DM worker runs inside the FastAPI process when `WORKER_ENABLED=true`. It uses the MySQL-backed `dm_jobs` table as the queue, so there is no separate queue service to start.

## Environment Variables

- `DATABASE_URL`: SQLAlchemy URL. Use `mysql+pymysql://username:password@host:3306/linkplease?charset=utf8mb4`.
- `PSEUDOGRAM_BASE_URL`: defaults to `https://pseudogram-api.onrender.com`.
- `PSEUDOGRAM_API_KEY`: also used as webhook HMAC secret.
- `WORKER_ENABLED`: set `false` for endpoint-only tests.
- `MAX_DM_ATTEMPTS`: default `6`.
- `RATE_LIMIT_REQUESTS`: default `10`.
- `RATE_LIMIT_WINDOW_SECONDS`: default `60`.
- `RECONCILE_AFTER_SECONDS`: default `5`.

Do not commit `.env` or real API keys.

## Tests

```bash
pytest -q
```

The tests cover rule creation, case-insensitive substring matching, valid/invalid webhook signatures, duplicate event handling, duplicate user/rule protection, deletion cancellation, stats, and retry/permanent-failure state transitions.

## Simulator

After deploying publicly and setting your real `PSEUDOGRAM_API_KEY`:

```bash
python scripts/simulate.py --webhook-url https://your-app.example.com/webhook --count 500 --duration-seconds 10
```

The script starts a simulation and prints the returned truth payload so you can compare it with `/stats`.

## Deployment

Deploy as a normal Python web service with an external MySQL 8 database. Set the environment variables above, expose the web process on `$PORT` or `8000`, and keep the service live for the assignment grading window.

Recommended deployment settings:

```bash
Python version: 3.12
Build command: pip install -r requirements.txt
Start command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Render currently defaults new Python services to a very new Python version. This repo pins Python with `.python-version` so Render uses Python 3.12 instead of the platform default.

## Known Limitations

See `FAILURES.md` for the honest failure-mode list.
