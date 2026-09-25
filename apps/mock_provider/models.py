from django.db import models


class MockProviderStatus(models.TextChoices):
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"


class MockProviderPayment(models.Model):
    """
    Simulates external payment provider database / ledger.
    Acts as the authoritative ground truth for reconciliation queries and webhook verification.
    """
    provider_ref = models.CharField(max_length=100, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=MockProviderStatus.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"MockPayment {self.provider_ref}: {self.status} (₹{self.amount})"
