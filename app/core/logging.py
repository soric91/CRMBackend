"""Structured logging configuration."""

import json
import logging
import sys
from typing import Any

_RESERVED_RECORD_KEYS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {
    # Uvicorn attaches an ANSI-coloured copy of its own message. It duplicates
    # `message` with escape codes a log pipeline cannot use.
    "color_message",
}


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON, including ``extra`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_KEYS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure the root logger once, replacing any previous handlers."""
    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn installs its own handlers and turns propagation off. The parent
    # `uvicorn` logger has to be included: clearing only the children leaves
    # their records reaching its handler, which is how half the output ends up
    # in Uvicorn's plain format while the application logs are JSON.
    for owned_by_uvicorn in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(owned_by_uvicorn)
        logger.handlers.clear()
        logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
