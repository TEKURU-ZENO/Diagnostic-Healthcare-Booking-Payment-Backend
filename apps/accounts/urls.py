from django.urls import path
from apps.accounts.views import (
    SignUpView,
    ThrottledTokenObtainPairView,
    ThrottledTokenRefreshView,
    MeView,
)

app_name = "accounts"

urlpatterns = [
    path("signup/", SignUpView.as_view(), name="signup"),
    path("login/", ThrottledTokenObtainPairView.as_view(), name="login"),
    path("token/", ThrottledTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("refresh/", ThrottledTokenRefreshView.as_view(), name="token_refresh"),
    path("me/", MeView.as_view(), name="me"),
]
