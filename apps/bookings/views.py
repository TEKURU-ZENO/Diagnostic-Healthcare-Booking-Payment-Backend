from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.serializers import BookingCreateSerializer, BookingDetailSerializer


class BookingViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post"]  # modifications occur through dedicated state actions

    def get_queryset(self):
        user = self.request.user
        base_qs = Booking.objects.select_related(
            "centre_test__centre", "centre_test__test", "user"
        ).prefetch_related("status_history").order_by("-created_at")

        # Crucial security guarantee: non-staff users strictly see only their own bookings.
        # Requesting another user's ID results in 404 Not Found, never leaking resource existence.
        if user.is_staff:
            return base_qs
        return base_qs.filter(user=user)

    def get_serializer_class(self):
        if self.action == "create":
            return BookingCreateSerializer
        return BookingDetailSerializer

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        booking = self.get_object()

        # Cannot cancel past appointments
        if booking.appointment_at <= timezone.now():
            raise ValidationError("Cannot cancel appointments that are in the past.")

        if booking.status == BookingStatus.CANCELLED:
            return Response(
                {"message": "Booking is already cancelled.", "booking": BookingDetailSerializer(booking).data},
                status=status.HTTP_200_OK,
            )

        booking.transition_to(BookingStatus.CANCELLED, source="user")
        return Response(
            {"message": "Booking cancelled successfully.", "booking": BookingDetailSerializer(booking).data},
            status=status.HTTP_200_OK,
        )
