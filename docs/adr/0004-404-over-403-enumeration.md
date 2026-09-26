# ADR 0004: Preventing Resource Enumeration via Uniform 404 Responses

## Status
Accepted (Amended)

## Context
When an authenticated user requests a resource they do not own (e.g. `GET /api/v1/bookings/42/`), returning `HTTP 403 Forbidden` confirms that resource #42 exists in the database. Malicious actors can iterate over sequential IDs to enumerate booking volume, active user accounts, and test patterns.

Furthermore, a subtle leakage vulnerability can occur if serializer-level validation and view-level authorization use different semantics. For example:
- In `PaymentCreateSerializer`, using `booking = serializers.PrimaryKeyRelatedField(queryset=Booking.objects.all())` validates existence across the entire table.
- A non-existent booking ID (e.g. `#999999`) fails serializer validation and yields `HTTP 400 Bad Request` ("object does not exist").
- A valid booking ID belonging to another user passes serializer validation and triggers `HTTP 404 Not Found` in the view.
- An attacker can exploit this discrepancy to test which booking IDs exist by checking whether the endpoint returns 400 or 404.

## Decision
We enforce authorization and existence checks uniformly:
1. In `PaymentCreateSerializer`, `booking` is declared as a plain integer (`serializers.IntegerField(min_value=1)`).
2. The view queries the database under a scoped filter:
   ```python
   booking = Booking.objects.select_for_update().filter(id=requested_booking_id).first()
   if not booking or (booking.user != request.user and not request.user.is_staff):
       raise Http404("Booking not found.")
   ```
3. Both non-existent booking IDs and foreign booking IDs consistently return `HTTP 404 Not Found`.

## Consequences
- Uniform 404 responses ensure external callers cannot differentiate between non-existent booking IDs and other users' booking IDs across both read and write endpoints.
- Mitigates sequential ID enumeration probing via HTTP status codes.
