"""JSON formatter and logging configuration."""

import json
import logging

import pytest

from app.core.logging import JsonFormatter, configure_logging, get_logger


def _record(**kwargs: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in kwargs.items():
        setattr(record, key, value)
    return record


class TestJsonFormatter:
    def test_emits_valid_json_with_core_fields(self) -> None:
        payload = json.loads(JsonFormatter().format(_record()))
        assert payload["level"] == "INFO"
        assert payload["logger"] == "app.test"
        assert payload["message"] == "hello world"
        assert "timestamp" in payload

    def test_extra_fields_are_included(self) -> None:
        payload = json.loads(JsonFormatter().format(_record(gateway_uuid="abc-123")))
        assert payload["gateway_uuid"] == "abc-123"

    def test_exception_info_is_rendered(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = _record()
            record.exc_info = sys.exc_info()

        payload = json.loads(JsonFormatter().format(record))
        assert "ValueError: boom" in payload["exception"]

    def test_non_serialisable_extra_falls_back_to_str(self) -> None:
        payload = json.loads(JsonFormatter().format(_record(obj=object())))
        assert payload["obj"].startswith("<object object")


class TestConfigureLogging:
    def test_sets_level_and_single_handler(self) -> None:
        configure_logging("WARNING")
        root = logging.getLogger()
        assert root.level == logging.WARNING
        assert len(root.handlers) == 1

    def test_repeated_calls_do_not_stack_handlers(self) -> None:
        configure_logging("INFO")
        configure_logging("INFO")
        assert len(logging.getLogger().handlers) == 1

    def test_plain_formatter_when_json_disabled(self) -> None:
        configure_logging("INFO", json_output=False)
        formatter = logging.getLogger().handlers[0].formatter
        assert not isinstance(formatter, JsonFormatter)

    def test_get_logger_returns_named_logger(self) -> None:
        assert get_logger("app.thing").name == "app.thing"

    @pytest.mark.parametrize("name", ["uvicorn", "uvicorn.access", "uvicorn.error"])
    def test_uvicorn_loggers_are_handed_over_to_the_root_handler(
        self, name: str
    ) -> None:
        """Otherwise access logs stay in Uvicorn's plain format under JSON."""
        logging.getLogger(name).addHandler(logging.NullHandler())

        configure_logging("INFO")

        logger = logging.getLogger(name)
        assert logger.handlers == []
        assert logger.propagate is True

    def test_uvicorn_records_reach_the_json_formatter(self) -> None:
        configure_logging("INFO", json_output=True)

        formatter = logging.getLogger().handlers[0].formatter
        assert isinstance(formatter, JsonFormatter)
        assert logging.getLogger("uvicorn.access").propagate is True


class TestNoiseFiltering:
    def test_uvicorns_ansi_duplicate_is_dropped(self) -> None:
        """`color_message` repeats the message with escape codes in it."""
        record = _record(color_message="\x1b[1mhello\x1b[0m")

        payload = json.loads(JsonFormatter().format(record))

        assert "color_message" not in payload
        assert payload["message"] == "hello world"
