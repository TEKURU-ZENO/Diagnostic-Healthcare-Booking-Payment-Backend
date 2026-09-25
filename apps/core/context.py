import contextvars

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="system"
)


def get_request_id() -> str:
    return request_id_var.get()


def set_request_id(req_id: str) -> None:
    request_id_var.set(req_id)
