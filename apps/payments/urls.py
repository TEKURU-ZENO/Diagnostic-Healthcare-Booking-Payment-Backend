from django.urls import path
from apps.payments.views import PaymentCreateView, WebhookView

app_name = "payments"

urlpatterns = [
    path("", PaymentCreateView.as_view(), name="payment_create"),
    path("webhook/", WebhookView.as_view(), name="payment_webhook"),
]
