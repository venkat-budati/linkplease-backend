# Remaining Failure Modes

1. If multiple independent application instances run workers against SQLite during tests, row locking is not strong enough to guarantee one worker claims a job. Production should use MySQL 8, where `FOR UPDATE SKIP LOCKED` is used. On SQLite this can cause duplicate external attempts, although the mock API idempotency key should usually return the same `dm_id`.

2. The outbound rate limiter is stored in the database, but it is implemented as a simple request log. With many processes and a weak database isolation level, two workers could reserve a slot at nearly the same time and briefly exceed 10 sends per 60 seconds. A dedicated MySQL lock table, `GET_LOCK`, or a Redis token bucket would reduce this risk.

3. If the mock API accepts a DM with `202` and then the application crashes before storing the returned `dm_id`, the job will retry using the same idempotency key. This should recover the original `dm_id` if the mock API honors idempotency indefinitely. If the provider loses the idempotency record, it could send a duplicate DM.

4. A `comment.deleted` event that arrives after the DM has already been accepted cannot cancel that DM because the mock API has no cancel/unsend endpoint. This can result in a DM being delivered for a deleted comment, but the local stats will still reflect the final delivered/failed state.

5. Rule changes are not versioned because the assignment only requires rule creation. A job stores the message text at creation time, so future rule editing would need explicit versioning to explain which message should be sent.

6. Reconciliation depends on the worker continuing to run. Queued jobs survive restart, but if the service is completely down for a long time, accepted DMs will remain counted as queued until the worker comes back and polls their final state.
