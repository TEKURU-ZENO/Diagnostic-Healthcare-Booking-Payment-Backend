from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.catalog.models import DiagnosticCentre, DiagnosticTest, CentreTest

User = get_user_model()


class Command(BaseCommand):
    help = "Seeds database with demo diagnostic centres, tests, pricing, and an admin user"

    def handle(self, *args, **options):
        self.stdout.write("Seeding demo data...")

        # 1. Admin user
        admin_user, created = User.objects.get_or_create(
            username="admin",
            defaults={"email": "admin@eve.health", "is_staff": True, "is_superuser": True},
        )
        if created:
            admin_user.set_password("AdminPass123!")
            admin_user.save()
            self.stdout.write(self.style.SUCCESS("Created admin user: admin / AdminPass123!"))

        # 2. Regular user
        test_user, created = User.objects.get_or_create(
            username="demouser",
            defaults={"email": "demo@eve.health"},
        )
        if created:
            test_user.set_password("DemoPass123!")
            test_user.save()
            self.stdout.write(self.style.SUCCESS("Created demo user: demouser / DemoPass123!"))

        # 3. Diagnostic Centres
        c1, _ = DiagnosticCentre.objects.get_or_create(
            name="Apex Diagnostic & Imaging Centre",
            city="Mumbai",
            defaults={"address": "12 Nariman Point, South Mumbai"},
        )
        c2, _ = DiagnosticCentre.objects.get_or_create(
            name="Metropolis Health Care Hub",
            city="Bengaluru",
            defaults={"address": "88 Koramangala 4th Block, Bengaluru"},
        )
        c3, _ = DiagnosticCentre.objects.get_or_create(
            name="Lifecare Diagnostic Lab",
            city="Delhi",
            defaults={"address": "45 Connaught Place, New Delhi"},
        )

        # 4. Diagnostic Tests
        t1, _ = DiagnosticTest.objects.get_or_create(
            code="CBC_01",
            defaults={"name": "Complete Blood Count (CBC)", "description": "Measures red/white blood cells and platelets"},
        )
        t2, _ = DiagnosticTest.objects.get_or_create(
            code="LIPID_01",
            defaults={"name": "Lipid Profile Panel", "description": "Cholesterol, HDL, LDL, triglycerides"},
        )
        t3, _ = DiagnosticTest.objects.get_or_create(
            code="THY_01",
            defaults={"name": "Thyroid Stimulating Hormone (TSH)", "description": "Assesses thyroid gland function"},
        )
        t4, _ = DiagnosticTest.objects.get_or_create(
            code="VITD_01",
            defaults={"name": "Vitamin D (25-Hydroxy)", "description": "Evaluates bone strength and immune health"},
        )

        # 5. Centre-Test Pricing (Notice: different centres charge differently!)
        pricing_data = [
            (c1, t1, Decimal("450.00")),
            (c1, t2, Decimal("850.00")),
            (c1, t3, Decimal("500.00")),
            (c1, t4, Decimal("1400.00")),
            (c2, t1, Decimal("400.00")),
            (c2, t2, Decimal("800.00")),
            (c2, t3, Decimal("450.00")),
            (c3, t1, Decimal("499.00")),
            (c3, t4, Decimal("1299.00")),
        ]

        for centre, test, price in pricing_data:
            ct, ct_created = CentreTest.objects.get_or_create(
                centre=centre,
                test=test,
                defaults={"price": price, "is_active": True},
            )
            if not ct_created:
                ct.price = price
                ct.save()

        self.stdout.write(self.style.SUCCESS("Database seeding completed successfully!"))
