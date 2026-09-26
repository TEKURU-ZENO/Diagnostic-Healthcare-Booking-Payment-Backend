import json
import uuid

from django.db import IntegrityError, transaction
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.bookings.models import Booking, BookingStatus
from apps.core.exceptions import (
    IdempotencyPayloadMismatchError,
    PaymentConflictError,
)
from apps.mock_provider.gateway import PaymentGateway
from apps.payments.models import Payment, PaymentStatus
from apps.payments.serializers import (
    PaymentCreateSerializer,
    PaymentSerializer,
)
from apps.payments.services import apply_payment_result, process_webhook_event


class PaymentCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # 1. Require Idempotency-Key header
        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key or not idempotency_key.strip():
            return Response(
                {
                    "error": {
                        "code": "missing_idempotency_key",
                        "message": "Idempotency-Key header is required.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        idempotency_key = idempotency_key.strip()

        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        requested_booking = serializer.validated_data["booking"]
        simulate_outcome = serializer.validated_data.get("simulate_outcome", "SUCCESS")

        # 2. Check for previously completed idempotent request first
        existing_payment = Payment.objects.filter(
            user=request.user, idempotency_key=idempotency_key
        ).first()
        if existing_payment:
            if existing_payment.booking_id != requested_booking.id:
                raise IdempotencyPayloadMismatchError(
                    "Idempotency-Key was already used for a different booking."
                )
            # Replay original response directly
            return Response(PaymentSerializer(existing_payment).data, status=status.HTTP_200_OK)

        # 3. Transaction 1: Lock booking, validate ownership & state, insert PENDING payment
        try:
            with transaction.atomic():
                # Lock booking row to serialize against background expiry and concurrent payments
                booking = (
                    Booking.objects.select_for_update()
                    .filter(id=requested_booking.id)
                    .first()
                )
                if not booking or (booking.user != request.user and not request.user.is_staff):
                    # Zero existence leakage: return 404 if booking not owned by caller
                    raise Http404("Booking not found.")

                if booking.status in (BookingStatus.CONFIRMED, BookingStatus.CANCELLED):
                    raise PaymentConflictError(
                        f"Cannot initiate payment for booking with status '{booking.status}'."
                    )

                if booking.status == BookingStatus.FAILED:
                    booking.transition_to(BookingStatus.PENDING, source="api")

                # Generate local merchant reference
                provider_ref = f"pay_{uuid.uuid4().hex}"

                # Insert-first in nested savepoint to eliminate race conditions
                try:
                    with transaction.atomic():
                        payment = Payment.objects.create(
                            user=request.user,
                            booking=booking,
                            amount=booking.amount,
                            idempotency_key=idempotency_key,
                            provider_ref=provider_ref,
                            status=PaymentStatus.PENDING,
                        )
                except IntegrityError:
                    # Race condition check: another simultaneous request inserted first
                    existing = Payment.objects.filter(
                        user=request.user, idempotency_key=idempotency_key
                    ).first()
                    if existing:
                        if existing.booking_id != booking.id:
                            raise IdempotencyPayloadMismatchError(
                                "Idempotency-Key was already used for a different booking."
                            )
                        return Response(PaymentSerializer(existing).data, status=status.HTTP_200_OK)

                    raise PaymentConflictError(
                        "Another payment is currently in-flight for this booking."
                    )
        except Http404:
            raise
        except (PaymentConflictError, IdempotencyPayloadMismatchError):
            raise

        # 4. Transaction 1 committed PENDING payment before calling gateway.
        # Call external payment gateway without holding any DB locks or open transactions.
        try:
            gateway_result = PaymentGateway.charge(
                provider_ref=payment.provider_ref,
                amount=payment.amount,
                simulate_outcome=simulate_outcome,
            )
        except Exception:
            # External gateway timed out or failed to respond; payment remains PENDING
            return Response(
                {
                    "status": PaymentStatus.PENDING,
                    "message": "Payment processing initiated with external provider.",
                    "payment": PaymentSerializer(payment).data,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        # 5. Transaction 2: Settle payment outcome via single funnel
        updated_payment, _ = apply_payment_result(
            payment=payment,
            status=gateway_result["status"],
            source="api",
        )

        return Response(PaymentSerializer(updated_payment).data, status=status.HTTP_200_OK)


class WebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        raw_body = request.body
        signature_header = request.headers.get("X-Webhook-Signature")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            return Response(
                {"error": "Invalid JSON payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        success, message, code = process_webhook_event(
            raw_body=raw_body,
            signature_header=signature_header,
            payload=payload,
        )

        return Response({"status": message}, status=code)
