import json

from django.conf import settings
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.payments.serializers import (
    PaymentCreateSerializer,
    PaymentSerializer,
)
from apps.payments.services import (
    BookingNotFound,
    initiate_payment,
    process_webhook_event,
)


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
        requested_booking_id = serializer.validated_data["booking"]

        if not getattr(settings, "MOCK_PAYMENTS_ENABLED", False):
            return Response(
                {
                    "error": {
                        "code": "gateway_unavailable",
                        "message": "Live payment gateway is not configured.",
                        "details": None,
                    }
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        simulate_outcome = serializer.validated_data.get("simulate_outcome") or "SUCCESS"

        try:
            payment, http_status = initiate_payment(
                user=request.user,
                booking_id=requested_booking_id,
                idempotency_key=idempotency_key,
                simulate_outcome=simulate_outcome,
            )
        except BookingNotFound:
            raise Http404("Booking not found.")

        return Response(PaymentSerializer(payment).data, status=http_status)


class WebhookView(APIView):
    # Authenticated by HMAC, not JWT. No throttling: the global anon rate (100/min) would answer
    # a provider's retry burst with 429s, and every 429 is a delayed or lost payment update.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

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
