from django.apps import AppConfig


class MockProviderConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mock_provider"
