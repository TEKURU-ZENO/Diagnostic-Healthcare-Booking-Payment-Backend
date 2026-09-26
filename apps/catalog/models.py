from decimal import Decimal

from django.db import models

from apps.core.models import TimeStampedModel


class DiagnosticCentre(TimeStampedModel):
    name = models.CharField(max_length=255, db_index=True)
    city = models.CharField(max_length=100, db_index=True)
    address = models.TextField()

    def __str__(self):
        return f"{self.name} ({self.city})"


class DiagnosticTest(TimeStampedModel):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    description = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.name} [{self.code}]"


class CentreTest(TimeStampedModel):
    centre = models.ForeignKey(
        DiagnosticCentre,
        on_delete=models.CASCADE,
        related_name="centre_tests",
    )
    test = models.ForeignKey(
        DiagnosticTest,
        on_delete=models.CASCADE,
        related_name="centre_tests",
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Price for this test at this specific diagnostic centre",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "test"],
                name="uniq_centre_test",
            ),
            models.CheckConstraint(
                condition=models.Q(price__gt=Decimal("0.00")),
                name="check_centre_test_price_positive",
            ),
        ]

    def __str__(self):
        return f"{self.centre.name} - {self.test.name} (₹{self.price})"
