import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.core.context import set_request_id


class RequestIDMiddleware:
    """
    Middleware that ensures every incoming request has a unique Request ID.
    Reads 'X-Request-ID' header or generates a new UUID4.
    Injects the ID into contextvars and sets 'X-Request-ID' on the response.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        req_id = request.headers.get("X-Request-ID")
        if not req_id:
            req_id = str(uuid.uuid4())

        set_request_id(req_id)
        request.request_id = req_id  # type: ignore[attr-defined]

        response = self.get_response(request)
        response["X-Request-ID"] = req_id
        return response
