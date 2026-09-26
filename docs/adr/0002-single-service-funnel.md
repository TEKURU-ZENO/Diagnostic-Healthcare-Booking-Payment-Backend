# ADR 0002: Single Canonical Service Funnel (`apply_payment_result`) & Payment State Machine

## Status
Accepted (Amended)

## Context
Payment outcomes can originate from three separate sources:
1. Synchronous gateway response during `POST /api/v1/payments/`.
2. Asynchronous webhook notifications delivered by the payment provider.
3. Automated reconciliation jobs recovering dropped or delayed webhooks.

Splitting state transition logic across views and workers leads to subtle discrepancies, inconsistent logging, and fractured state machine handling. However, merely channeling requests through a single function does not prevent race conditions if the payment entity itself lacks an internal state machine.

Specifically, in the standard payment lifecycle:
- A user completes payment via synchronous checkout -> `apply_payment_result()` marks the payment `SUCCESS` and transitions the booking to `CONFIRMED`.
- Moments later, the payment provider delivers its standard `SUCCESS` webhook.
- If the service function only evaluates `if booking.status == CONFIRMED and status == SUCCESS: flag_for_refund()`, it misinterprets the provider's ordinary webhook delivery as a duplicate capture and wrongly flags the user's only payment for refund!
- Conversely, a stale `FAILED` webhook arriving after `SUCCESS` would overwrite the payment row to `FAILED`, incorrectly indicating money was never taken.

## Decision
We enforce a strict, unidirectional payment-level state machine alongside the booking state machine, with consistent lock ordering:

1. **Lock Ordering & Stale Read Protection**:
   `Booking` is always locked first via `select_for_update()`, followed by `Payment` via `select_for_update()`. This consistent ordering prevents deadlocks with `POST /payments/` and ensures the service always operates on authoritative database state.

2. **Unidirectional Payment Lifecycle**:
   - `Payment.status` can never transition backwards. `SUCCESS` is terminal: money has been captured and subsequent `FAILED` notifications for that payment are logged and ignored.
   - If `payment.status == status`, the event is an idempotent replay (e.g. sync checkout followed by webhook, or duplicate webhooks) and executes as an immediate no-op.

3. **True Second Capture Detection**:
   A payment is flagged for refund (`payment.flagged_for_refund = True`) only when a genuinely distinct payment row changes state to `SUCCESS` against an already `CONFIRMED` or `CANCELLED` booking.

4. **Webhook Status Whitelist**:
   Only `SUCCESS` and `FAILED` are accepted terminal outcomes. Any other webhook status (e.g. `REFUNDED`, `PENDING`, or vendor-specific states) is marked `IGNORED` and does not alter payment or booking records.

## Consequences
- Idempotent replay safety across overlapping synchronous and asynchronous deliveries.
- Elimination of false refund flags during normal payment operations.
- Accurate ledger alignment between the application and external payment providers.
