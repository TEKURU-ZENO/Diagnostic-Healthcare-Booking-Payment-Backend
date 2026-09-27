# Roadmap: What to Build Next

This document outlines the next technical steps for this service beyond the core prototype. Ordered by dependency: reliability infrastructure first, then real payment gateways, financial ledgering, domain scheduling, observability, and compliance.

---

## 1. Reliability & Asynchronous Foundation
- **Transactional Outbox**: Notifications (SMS, WhatsApp, email) and refund triggers must never run inside the database transaction. Add an `OutboxEvent` table committed atomically with domain state, polled by a worker using `SELECT FOR UPDATE SKIP LOCKED`.
- **At-Least-Once Consumer Idempotency**: Because the outbox worker can redeliver after worker crashes, downstream consumers must deduplicate: use the refund ID as the payment gateway's idempotency key, and hash `(recipient, template, event_id)` for messaging gateways.
- **Two-Tier Webhook Ingestion**: Webhooks currently execute business logic synchronously to return 404 on missing references so the provider retries. When splitting ingestion into an immediate 200 fast-ACK, the provider stops retrying; our worker must handle retries, exponential backoff, and a dead-letter queue (DLQ) internally.

---

## 2. Integer Minor Units & Currency Exponents
- **ISO 4217 Compliance**: Replace `DecimalField` with integer minor units (`amount_minor = models.PositiveBigIntegerField()`) and a currency code (`currency = models.CharField(max_length=3)`). Different currencies have different exponents (INR and USD have 2, JPY has 0, KWD has 3).
- **Migration Strategy**: Add the column, backfill via script, update application code to write both, switch reads to minor units, and drop the decimal column (expand, backfill, contract).

---

## 3. Real Payment Gateway Adapters
- **Realistic Gateway Lifecycle**: Real gateways (Razorpay, Stripe) do not use a synchronous server-side `charge()`. The server creates an order or intent, the client completes payment (handling 3DS and UPI prompts), and the server verifies signatures or processes webhooks.
  - Interface: `create_order()`, `verify_client_confirmation()`, `fetch_status()`, `refund()`, and `parse_webhook()`.
- **UPI Reconciliation Window**: UPI payments often remain pending for several minutes while the user switches to their UPI app. Reconciliation thresholds must account for this before treating pending transactions as abandoned.
- **Provider-Specific Webhook Freshness**: Stripe includes a signed timestamp in its header, allowing a 5-minute replay window check. Razorpay signs only the body, and retried events retain their original timestamp; freshness checks must be provider-specific, relying on our `WebhookEvent.event_id` unique constraint as the universal replay defense.

---

## 4. Refund Engine & Partial Refunds
- **Refund Lifecycle**: Add a `Refund` model tracking `PENDING`, `PROCESSING`, `SUCCEEDED`, and `FAILED` states.
- **Financial Controls**: A database constraint enforcing `SUM(refunded) <= captured_amount` to prevent over-refunds.
- **Operational Choice**: Not all slot collisions should automatically refund; support an operational workflow where customer support can offer an alternative slot before triggering a gateway refund.

---

## 5. Double-Entry Accounting Ledger & Settlement
- **Per-Transaction Balancing**: Avoid global balance checks. Every financial movement generates an immutable journal entry where `SUM(debits) - SUM(credits) == 0` enforced within that transaction. Corrections use reversing entries, never row updates.
- **Gateway Fee Accounting**: Account for gateway processing fees (`Payment_Gateway_Fee`) alongside gross customer receipts and net payouts.
- **T+1 Settlement File Ingestion**: Ingest daily settlement reports from the provider to detect chargebacks, fee variances, and payments missed by both webhooks and status polling.

---

## 6. Diagnostic Scheduling & Slot Capacity
- **Slot Table & Capacity Drift**: Add a `Slot` model with a capacity limit. Protect against drift between `booked_count` and actual rows via a database `CHECK (booked_count <= capacity)` and a periodic validation query.
- **Seat Holds with TTL**: Temporary reservations before payment with automated expiry. If a hold expires and the seat is rebooked before a delayed payment arrives, route through the existing refund-flag path.
- **Local Time Opening Hours**: Store recurring clinic hours in local wall-clock time with the centre's time zone, keeping individual appointment instances in UTC.

---

## 7. Scaling, Multi-Tenancy & Observability
- **Connection Pooling**: Add PgBouncer in front of PostgreSQL to handle connection spikes during traffic surges.
- **Table Partitioning**: Range-partition `WebhookEvent` and `BookingStatusHistory` by `created_at` (monthly) to keep indexes compact as audit logs grow into millions of rows.
- **Scoped RBAC**: Replace global `is_staff` with centre-scoped role-based access control so lab staff can only view and manage bookings for their specific diagnostic centre.
- **Metrics & Alerts**: Prometheus metrics tracking duplicate webhooks, reconciliation settlements, and open refund flags. Alerting on stuck PENDING payments or unhandled refund flags > 0.
- **Distributed Tracing**: OpenTelemetry instrumentation carrying the existing `X-Request-ID` across HTTP requests, background tasks, and external calls.

---

## 8. Healthcare Integrations & Data Protection (India)
- **ABDM (Ayushman Bharat Digital Mission)**: Act as a Health Information Provider (HIP) under Milestone 2 (M2) to link care contexts and share diagnostic test reports with patient consent via ABHA ID. Register the lab in the Health Facility Registry (HFR).
- **FHIR R4 Modeling**: Map diagnostic orders to FHIR `ServiceRequest` and lab results to FHIR `DiagnosticReport`.
- **DPDP Act & Field-Level Encryption**: Encrypt sensitive patient PII (phone number, address) with AES-256-GCM using envelope encryption with a KMS. Maintain an HMAC blind index on phone numbers to allow fast, indexed lookups without storing plaintext numbers.
