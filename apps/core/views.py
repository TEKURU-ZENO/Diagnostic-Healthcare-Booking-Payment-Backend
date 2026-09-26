from django.db import connection
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    """
    Service health check endpoint at /healthz.
    Verifies database connectivity and returns 200 OK or 503 Service Unavailable.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                row = cursor.fetchone()
                db_healthy = row is not None and row[0] == 1
        except Exception as e:
            return Response(
                {"status": "error", "database": "disconnected", "detail": str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if db_healthy:
            return Response(
                {"status": "ok", "database": "connected"},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"status": "error", "database": "unresponsive"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
