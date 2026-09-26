from typing import Any

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler


class InvalidStateTransitionError(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Invalid state transition requested."
    default_code = "invalid_state_transition"


class PaymentConflictError(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Payment conflict: payment is already processed or in-flight."
    default_code = "payment_conflict"


class IdempotencyPayloadMismatchError(APIException):
    status_code = 422
    default_detail = "Idempotency key was previously used with different parameters."
    default_code = "idempotency_payload_mismatch"


class PaymentNotFoundError(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Payment reference not found."
    default_code = "payment_not_found"


def custom_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """
    Custom exception handler to standardize all API error responses into:
    {
        "error": {
            "code": "<error_code>",
            "message": "<human_readable_message>",
            "details": <dict_or_list_or_null>
        }
    }
    """
    response = exception_handler(exc, context)

    if response is not None:
        code = getattr(exc, "default_code", getattr(response, "status_text", "error"))
        message = "An error occurred."
        details = response.data

        if isinstance(response.data, dict):
            if "detail" in response.data:
                message = str(response.data["detail"])
                details = None
            else:
                message = "Validation failed."
        elif isinstance(response.data, list):
            message = "Validation failed."

        response.data = {
            "error": {
                "code": str(code),
                "message": message,
                "details": details,
            }
        }

    return response
