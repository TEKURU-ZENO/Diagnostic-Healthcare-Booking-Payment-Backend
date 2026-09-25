import json
import logging
from datetime import datetime, timezone
from apps.core.context import get_request_id


class StructuredJsonFormatter(logging.Formatter):
    """
    Format log records as structured JSON including request_id,
    and optional domain metadata (booking_id, event_id, provider_ref).
    Preserves privacy: does not log patient health PII.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", get_request_id()),
        }

        # Include custom domain context if present
        for field in ("booking_id", "event_id", "provider_ref", "user_id"):
            val = getattr(record, field, None)
            if val is not None:
                log_data[field] = str(val)

        if record.exc_info:
            log_data["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_data)
