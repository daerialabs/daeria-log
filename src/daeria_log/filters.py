"""Logging filters: context enrichment and secret redaction."""

# region Imports & Config

# python imports
import logging
import os
import re
import socket
from typing import Any

# daeria_log imports
from daeria_log.context import current_context

REDACTED = "***"

# Keys are split into words (snake_case, kebab-case, camelCase) and compared
# as whole words, so ``api_key`` and ``accessToken`` are masked while
# ``max_tokens``, ``passenger_count`` or a render ``pass_name`` are not.
_SENSITIVE_WORDS = frozenset(
    {
        "password",
        "passwd",
        "passphrase",
        "secret",
        "token",
        "apikey",
        "authorization",
        "credential",
        "credentials",
    }
)
# Two consecutive words that are sensitive once joined (``api`` + ``key``).
_SENSITIVE_PAIRS = frozenset({"apikey", "accesskey", "privatekey", "signingkey"})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

URL_CREDENTIALS = re.compile(r"(\w+://[^:/\s@]+:)[^@\s]+(@)")

# ``key=value``, ``key: value``, ``"key": "value"`` inside free text. The key
# is checked with ``is_sensitive_key``; an optional auth scheme (``Bearer``)
# is masked together with its value.
KEY_VALUE = re.compile(
    r"""([\w.-]+)(["']?\s*[=:]\s*)"""
    r"""((?:(?:bearer|basic|digest)\s+)?(?:"[^"]*"|'[^']*'|[^\s,;&"']+))""",
    re.IGNORECASE,
)

# endregion

# ==========

# region Redaction helpers


def is_sensitive_key(key: Any) -> bool:
    """Tell whether a field name looks like it holds a secret.

    Args:
        key: A field name (``api_key``, ``accessToken``, ``X-Api-Key``...).

    Returns:
        bool: True if one of its words (or a pair of words) is sensitive.
    """
    words = [
        word
        for word in _NON_ALNUM.split(_CAMEL_BOUNDARY.sub("_", str(key)).lower())
        if word
    ]
    for index, word in enumerate(words):
        if word in _SENSITIVE_WORDS:
            return True
        if index + 1 < len(words) and word + words[index + 1] in _SENSITIVE_PAIRS:
            return True
    return False


def _mask_pair(match: re.Match[str]) -> str:
    """Mask the value of a ``key=value`` match if the key is sensitive.

    Args:
        match: A match of ``KEY_VALUE``.

    Returns:
        str: The masked pair, or the original text.
    """
    key, separator, _ = match.groups()
    if is_sensitive_key(key):
        return f"{key}{separator}{REDACTED}"
    return match.group(0)


def redact_text(text: str) -> str:
    """Mask secrets in free text.

    Two kinds are handled: the password of a ``scheme://user:password@host``
    URL, and the value of ``key=value`` pairs whose key is sensitive (query
    strings, ``Authorization: Bearer ...``, ``password=...``). Command line
    flags written as ``--password value`` are not detected.

    Args:
        text: Text that may contain secrets.

    Returns:
        str: The text with secrets replaced by ``***``.
    """
    text = URL_CREDENTIALS.sub(rf"\1{REDACTED}\2", text)
    return KEY_VALUE.sub(_mask_pair, text)


def redact_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Mask sensitive values of a fields mapping.

    Args:
        fields: Structured fields of a record.

    Returns:
        dict[str, Any]: A copy where sensitive keys are masked and secrets in
            string values are scrubbed.
    """
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if is_sensitive_key(key):
            clean[key] = REDACTED
        else:
            clean[key] = _redact_value(value)
    return clean


def _redact_value(value: Any) -> Any:
    """Redact a value, descending into mappings and sequences.

    Args:
        value: Any field value.

    Returns:
        Any: The value with secrets masked.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return redact_fields(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


# endregion

# ==========

# region Filters


class ContextFilter(logging.Filter):
    """Attach process identity and the ambient context to each record."""

    def __init__(self, app: str, session_id: str) -> None:
        """Initialize the filter.

        Args:
            app: Application name (``maya``, ``desktop``...).
            session_id: Id shared by every record of this process run.
        """
        super().__init__()
        self._app = app
        self._session_id = session_id
        self._host = socket.gethostname()

    def filter(self, record: logging.LogRecord) -> bool:
        """Enrich the record.

        Args:
            record: The record being processed.

        Returns:
            bool: Always True.
        """
        record.daeria_app = self._app
        record.daeria_session = self._session_id
        record.daeria_host = self._host
        record.daeria_pid = os.getpid()
        record.daeria_context = current_context()
        if not hasattr(record, "daeria_fields"):
            record.daeria_fields = {}
        return True


class RedactionFilter(logging.Filter):
    """Mask secrets in messages, fields and context before they are written."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the record in place.

        Args:
            record: The record being processed.

        Returns:
            bool: Always True.
        """
        message = record.getMessage()
        clean = redact_text(message)
        if clean != message:
            record.msg, record.args = clean, ()
        record.daeria_fields = redact_fields(getattr(record, "daeria_fields", {}))
        record.daeria_context = redact_fields(getattr(record, "daeria_context", {}))
        return True


# endregion
