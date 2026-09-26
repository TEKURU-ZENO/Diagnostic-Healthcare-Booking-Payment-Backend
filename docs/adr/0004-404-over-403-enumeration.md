# ADR 0004: Returning 404 Not Found Instead of 403 Forbidden to Prevent Resource Enumeration

## Status
Accepted

## Context
When an authenticated user requests a resource they do not own (e.g. `GET /api/v1/bookings/42/`), returning `HTTP 403 Forbidden` confirms that resource #42 exists in the database. Malicious actors can iterate over sequential IDs to enumerate booking volume, active user accounts, and test patterns.

## Decision
We enforce authorization at the queryset filtering level:
```python
def get_queryset(self):
    if self.request.user.is_staff:
        return Booking.objects.all()
    return Booking.objects.filter(user=self.request.user)
```
When a user requests a booking belonging to another user, Django's `get_object()` fails to locate the record within the user's filtered queryset and raises standard `Http404` (`404 Not Found`).

## Consequences
- **Zero Information Leakage**: An attacker cannot distinguish between a non-existent booking ID and another user's booking ID.
- Prevents enumeration attacks across all customer-facing endpoints.
