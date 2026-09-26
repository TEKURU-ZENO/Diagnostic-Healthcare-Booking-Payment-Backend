# ADR 0002: Single Canonical Service Funnel (`apply_payment_result`)

## Status
Accepted

## Context
Payment outcomes can originate from three separate sources:
1. Synchronous gateway response during `POST /api/v1/payments/`.
2. Asynchronous webhook notifications delivered by the payment provider.
3. Automated reconciliation jobs recovering dropped or delayed webhooks.

Splitting state transition logic across views and workers leads to subtle discrepancies, inconsistent logging, and fractured state machine handling.

## Decision
We route all payment settlement outcomes through a single service function:
```python
@transaction.atomic
def apply_payment_result(payment, status, source, event_id=None):
    ...
```

Rules enforced uniformly in this funnel:
- Uses `select_for_update()` on the `Booking` record to serialize concurrent updates.
- If `booking.status == CONFIRMED` and an out-of-order `FAILED` arrives, it is logged and ignored.
- If `booking.status == CONFIRMED` and a second `SUCCESS` arrives (double charge), the payment is flagged for refund (`flagged_for_refund = True`).
- If `booking.status == FAILED` and late `SUCCESS` arrives, the booking is confirmed (or flagged for refund if the slot was rebooked in the interim).
- If `booking.status == CANCELLED` and `SUCCESS` arrives, the payment is flagged for refund.

## Consequences
- Single location to test and audit for payment state logic.
- Eliminates race conditions between simultaneous webhook delivery and synchronous redirect responses.
