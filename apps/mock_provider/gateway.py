from decimal import Decimal

from apps.mock_provider.models import MockProviderPayment, MockProviderStatus


class GatewayTimeout(Exception):
    """The provider accepted the charge but we never got its answer. Only a webhook or
    reconciliation can tell us the outcome."""


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
        # "ASYNC_SUCCESS" / "ASYNC_FAILED": the provider records the outcome, but the response
        # is lost, so our payment stays PENDING. This is what makes webhooks necessary.
        requested = simulate_outcome.upper()
        is_async = requested.startswith("ASYNC_")
        outcome = (
            MockProviderStatus.SUCCESS
            if requested.removeprefix("ASYNC_") == "SUCCESS"
            else MockProviderStatus.FAILED
        )

        mock_payment, _ = MockProviderPayment.objects.get_or_create(
            provider_ref=provider_ref,
            defaults={"amount": amount, "status": outcome},
        )
        if is_async:
            raise GatewayTimeout(provider_ref)
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
