# ADR 0001: Database Unique Constraints for Webhook Deduplication and Idempotency

## Status
Accepted

## Context
Payment gateways frequently deliver webhook notifications multiple times due to network timeouts, retries, or distributed dispatchers. In high-concurrency environments, two identical webhook events can arrive within milliseconds of each other.

Common approaches include:
1. **Application-level read-then-write check (`if already_processed: return`)**: Vulnerable to race conditions where both concurrent requests execute the read before either writes, resulting in duplicate state transitions and double charges.
2. **Distributed locks (e.g. Redis `SETNX`)**: Introduces external network dependency, requires TTL tuning, and can leave locks dangling if workers terminate unexpectedly.

## Decision
We enforce deduplication directly at the PostgreSQL database engine layer:
1. `WebhookEvent.event_id` carries a database `UNIQUE` constraint and index.
2. Webhook ingestion attempts to insert the event inside a transaction savepoint.
3. If a duplicate `event_id` is received, the database raises an `IntegrityError`. The application catches this exception and returns HTTP 200 OK immediately with a `duplicate` status.
4. Client idempotency for `POST /payments/` is enforced via a scoped `UniqueConstraint(fields=["user", "idempotency_key"])`.

## Consequences
- **Atomicity**: The database engine guarantees that exactly one insertion succeeds under any concurrency level.
- **Resilience**: Zero external cache or lock manager dependency.
- **Replay Safety**: Duplicate webhook deliveries are completely idempotent no-ops.
