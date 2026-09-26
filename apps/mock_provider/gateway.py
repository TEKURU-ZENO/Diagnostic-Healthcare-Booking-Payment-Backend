from decimal import Decimal

from apps.mock_provider.models import MockProviderPayment, MockProviderStatus


class PaymentGateway:
    """
    In-process payment gateway adapter.
    Encapsulates mock payment provider operations without making self-referential HTTP calls,
    preventing worker deadlocks while enabling simple swapping for Stripe / Razorpay.
    """

    @classmethod
    def charge(
        cls,
        provider_ref: str,
        amount: Decimal,
        simulate_outcome: str = "SUCCESS",
    ) -> dict:
        """
        Executes a deterministic charge on the mock provider.
        """
        outcome = (
            MockProviderStatus.SUCCESS
            if simulate_outcome.upper() == "SUCCESS"
            else MockProviderStatus.FAILED
        )

        mock_payment, _ = MockProviderPayment.objects.get_or_create(
            provider_ref=provider_ref,
            defaults={"amount": amount, "status": outcome},
        )
        return {
            "provider_ref": mock_payment.provider_ref,
            "status": mock_payment.status,
            "amount": str(mock_payment.amount),
        }

    @classmethod
    def get_status(cls, provider_ref: str) -> dict | None:
        """
        Queries authoritative status from external provider ledger.
        Used by the reconciliation service.
        """
        try:
            payment = MockProviderPayment.objects.get(provider_ref=provider_ref)
            return {
                "provider_ref": payment.provider_ref,
                "status": payment.status,
                "amount": str(payment.amount),
                "created_at": payment.created_at.isoformat(),
            }
        except MockProviderPayment.DoesNotExist:
            return None
