# ADR 0006: Premature Webhook Race & Payment Creation Boundary

## Status
Accepted

## Context
When processing customer checkout in `POST /payments/`, external payment gateways can fire asynchronous webhook notifications before the initiating HTTP request has finished executing.

If the initiating endpoint executes in a single database transaction:
1. The endpoint locks the booking and inserts a `PENDING` payment.
2. The endpoint invokes the external gateway network call while holding the transaction open.
3. The gateway processes the transaction and fires a webhook immediately.
4. The webhook arrives at our server while the initiating transaction has not yet committed.
5. The webhook searches for `Payment.objects.filter(provider_ref=...)` and finds nothing.
6. If the webhook records this as `IGNORED` and returns 200, the payment record is lost forever when the initiating transaction finishes or fails.

## Decision
We resolve this race condition through three architectural guarantees:

1. **Short Pre-Invocation Transaction Boundary**:
   In `POST /payments/`, we generate a local merchant reference (`provider_ref = f"pay_{uuid.uuid4().hex}"`) and commit the `PENDING` payment row in its own short database transaction **before** calling the external payment gateway. We never hold an open database lock or transaction while awaiting third-party network responses.

2. **Rollback on Unknown Provider Reference**:
   If a webhook arrives before the payment row is committed, `process_webhook_event()` rolls back the database transaction (`transaction.set_rollback(True)`) and returns `HTTP 404 Not Found`. This prevents the `WebhookEvent` row from persisting as a duplicate and prompts the payment provider to retry with exponential backoff.

3. **In-Flight Payment Constraint**:
   To prevent concurrent checkout requests from generating multiple in-flight payments for the same booking, a partial database unique constraint is enforced:
   ```python
   models.UniqueConstraint(
       fields=["booking"],
       condition=models.Q(status="PENDING"),
       name="one_inflight_payment_per_booking",
   )
   ```

## Consequences
- No database locks are held over external network I/O.
- Out-of-order webhook delivery prior to local commit triggers safe provider retries instead of silent data loss.
- Periodic reconciliation acts as the final safety net for any dropped retries.
