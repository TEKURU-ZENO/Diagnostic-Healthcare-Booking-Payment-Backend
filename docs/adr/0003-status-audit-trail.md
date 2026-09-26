# ADR 0003: Immutable Append-Only Status Audit Trail

## Status
Accepted

## Context
In healthcare bookings and financial transactions, understanding "who changed what, when, and via which source" is critical for dispute resolution, patient safety, and regulatory compliance.

## Decision
We implement an append-only `BookingStatusHistory` table:
- Fields: `booking`, `from_status`, `to_status`, `source`, `event_id`, `created_at`.
- Updates occur automatically inside `Booking.transition_to()` within the exact same database transaction as the status update.

## Consequences
- Full traceability for every transition (e.g. `api`, `webhook`, `reconciliation`, `system`).
- Enables immediate inspection during payment disputes.
- Tamper-proof history decoupled from in-place updates.
