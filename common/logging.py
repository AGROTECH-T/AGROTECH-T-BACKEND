"""Formatea registros de aplicación como JSON.

@author Cristian Deysdayr Jimenez
"""
import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Convierte cada registro en una línea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Serializa nivel, origen, fecha y mensaje del registro."""
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
