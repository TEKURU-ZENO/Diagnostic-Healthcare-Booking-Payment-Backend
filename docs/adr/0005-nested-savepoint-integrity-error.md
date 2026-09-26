# ADR 0005: Nested Savepoints for PostgreSQL IntegrityError Handling

## Status
Accepted

## Context
In PostgreSQL, when a database query raises an exception (such as `IntegrityError` due to a unique constraint violation), PostgreSQL marks the current transaction as aborted (`current transaction is aborted, commands ignored until end of transaction block`).

Attempting to catch `IntegrityError` inside a standard `@transaction.atomic` block without a savepoint causes all subsequent queries in the same function to fail with `TransactionManagementError`.

## Decision
We wrap the specific insertion of `WebhookEvent` in a nested atomic block:
```python
@transaction.atomic  # Outer transaction
def process_webhook_event(...):
    try:
        with transaction.atomic():  # Creates a SAVEPOINT in Postgres
            event = WebhookEvent.objects.create(event_id=event_id, payload=payload)
    except IntegrityError:
        return True, "duplicate", 200

    # Outer transaction remains healthy and can execute subsequent queries!
    apply_payment_result(...)
    event.status = WebhookEventStatus.PROCESSED
    event.save()
```

## Consequences
- The nested `transaction.atomic()` establishes a database `SAVEPOINT`.
- On duplicate collision, Postgres rolls back only to the savepoint, leaving the outer transaction clean and active.
- If downstream processing crashes, the outer transaction rolls back both the event insertion and the state change, ensuring the payment provider's retry will be processed instead of prematurely deduped.
