"""Record formatters: JSON lines for machines, plain text for humans."""

# region Imports & Config

# python imports
import json
import logging
from datetime import UTC, datetime
from typing import Any

# daeria_log imports
from daeria_log.filters import redact_text
from daeria_log.logger import public_target

# endregion

# ==========

# region Record conversion

SCHEMA_VERSION = 1


def record_to_dict(record: logging.LogRecord) -> dict[str, Any]:
    """Convert a record into the Daeria log schema (see ``SCHEMA.md``).

    Args:
        record: A record that went through ``ContextFilter``.

    Returns:
        dict[str, Any]: The structured record.
    """
    timestamp = datetime.fromtimestamp(record.created, UTC)
    context = dict(getattr(record, "daeria_context", {}))
    data: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "ts": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": record.levelname,
        "target": public_target(record.name),
        "message": record.getMessage(),
        "fields": dict(getattr(record, "daeria_fields", {})),
        "app": getattr(record, "daeria_app", ""),
        "pid": getattr(record, "daeria_pid", record.process),
        "host": getattr(record, "daeria_host", ""),
        "session_id": getattr(record, "daeria_session", ""),
        "correlation_id": context.pop("correlation_id", None),
        "context": context,
        "caller": f"{record.filename}:{record.lineno}",
    }
    if record.exc_info:
        text = logging.Formatter().formatException(record.exc_info)
        data["exception"] = redact_text(text)
    return data


# endregion

# ==========

# region Formatters


class JsonFormatter(logging.Formatter):
    """Format records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a record.

        Args:
            record: The record to format.

        Returns:
            str: A single-line JSON document.
        """
        return json.dumps(record_to_dict(record), default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Format records as compact human readable lines."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a record.

        Args:
            record: The record to format.

        Returns:
            str: ``HH:MM:SS.mmm LEVEL target message key=value``.
        """
        data = record_to_dict(record)
        clock = data["ts"][11:23]
        line = f"{clock} {data['level']:<8} {data['target']}  {data['message']}"
        extras = {**data["context"], **data["fields"]}
        if data["correlation_id"]:
            extras = {"cid": data["correlation_id"], **extras}
        if extras:
            line += "  " + " ".join(f"{key}={value}" for key, value in extras.items())
        if "exception" in data:
            line += "\n" + data["exception"]
        return line


# endregion
