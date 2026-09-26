from decimal import Decimal

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.mock_provider.gateway import PaymentGateway


class MockChargeView(APIView):
    """
    HTTP endpoint simulating external payment gateway charge API.
    Used by chaos tests and standalone simulation scripts.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        provider_ref = request.data.get("provider_ref")
        amount_raw = request.data.get("amount")
        simulate_outcome = request.data.get("simulate_outcome", "SUCCESS")

        if not provider_ref or amount_raw is None:
            return Response(
                {"error": "Missing required fields 'provider_ref' and 'amount'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount = Decimal(str(amount_raw))
        result = PaymentGateway.charge(provider_ref, amount, simulate_outcome)
        return Response(result, status=status.HTTP_200_OK)


class MockStatusView(APIView):
    """
    HTTP endpoint querying provider ledger status by provider_ref.
    """
    permission_classes = [AllowAny]

    def get(self, request, provider_ref: str):
        result = PaymentGateway.get_status(provider_ref)
        if not result:
            return Response(
                {"error": "Payment reference not found in provider ledger"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(result, status=status.HTTP_200_OK)
