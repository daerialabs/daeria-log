"""Structured logger.

``get_logger(__name__)`` returns a thin wrapper over a stdlib logger.
Keyword arguments become structured fields.

Targets are whatever names the calling code chooses (usually ``__name__``).
Internally every stdlib logger lives under ``INTERNAL_ROOT``, so handlers can
be attached to one logger without touching the stdlib root logger, and
without daeria_log having to know which project it is embedded in. The
prefix is stripped again when a record is turned into a dict.
"""

# region Imports & Config

# python imports
import logging
from typing import Any

# daeria_log imports
from daeria_log.levels import TRACE

INTERNAL_ROOT = "_daeria"

TARGET_ALIASES: dict[str, str] = {
    "__main__": "main",
}

# endregion

# ==========

# region Targets


def register_alias(package: str, target: str) -> None:
    """Map a top-level Python package to a target prefix.

    Args:
        package: Top-level package name (e.g. ``"my_pkg_houdini"``).
        target: Target prefix (e.g. ``"myproject.houdini"``).
    """
    TARGET_ALIASES[package] = target


def target_for(name: str) -> str:
    """Convert a module ``__name__`` into a target.

    Args:
        name: Module or logger name.

    Returns:
        str: The target. Only the first segment is remapped, and only if an
            alias was registered for it; otherwise the name is kept as is.
    """
    head, _, tail = name.partition(".")
    base = TARGET_ALIASES.get(head, head)
    target = f"{base}.{tail}" if tail else base
    return target or "root"


def logger_name(target: str) -> str:
    """Return the stdlib logger name that backs a target.

    Args:
        target: A target, as returned by ``target_for``.

    Returns:
        str: The name under ``INTERNAL_ROOT``.
    """
    return f"{INTERNAL_ROOT}.{target}"


def public_target(name: str) -> str:
    """Return the target of a stdlib logger name.

    Args:
        name: A ``LogRecord.name``. Names of adopted third-party loggers do
            not live under ``INTERNAL_ROOT`` and are returned unchanged.

    Returns:
        str: The target without the internal prefix.
    """
    prefix = INTERNAL_ROOT + "."
    return name[len(prefix) :] if name.startswith(prefix) else name


# endregion

# ==========

# region Logger


class StructuredLogger:
    """Logger accepting stdlib-style messages plus keyword fields.

    Example:
        >>> log = get_logger(__name__)
        >>> log.info("Published %s", asset, version=3)
    """

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize the wrapper.

        Args:
            logger: The underlying stdlib logger.
        """
        self._logger = logger

    @property
    def name(self) -> str:
        """str: The target of this logger."""
        return public_target(self._logger.name)

    def child(self, suffix: str) -> "StructuredLogger":
        """Return a logger for a sub-target.

        Args:
            suffix: Sub-target name appended to this target.

        Returns:
            StructuredLogger: The child logger.
        """
        return StructuredLogger(self._logger.getChild(suffix))

    def is_enabled_for(self, level: int) -> bool:
        """Tell whether a level is enabled.

        Args:
            level: Numeric level.

        Returns:
            bool: True if records of that level would be emitted.
        """
        return self._logger.isEnabledFor(level)

    def _emit(
        self, level: int, msg: str, args: tuple, exc_info: Any, fields: dict
    ) -> None:
        """Emit a record. Every public method calls this, one frame deep.

        Args:
            level: Numeric level.
            msg: Message, optionally with ``%`` placeholders.
            args: Values for the placeholders.
            exc_info: Exception info, as in stdlib logging.
            fields: Structured fields attached to the record.
        """
        if self._logger.isEnabledFor(level):
            self._logger.log(
                level,
                msg,
                *args,
                exc_info=exc_info,
                stacklevel=3,  # skip _emit and the public method
                extra={"daeria_fields": fields},
            )

    def log(
        self,
        level: int,
        msg: str,
        *args: Any,
        exc_info: Any = None,
        **fields: Any,
    ) -> None:
        """Emit a record.

        Args:
            level: Numeric level.
            msg: Message, optionally with ``%`` placeholders.
            *args: Values for the placeholders.
            exc_info: Exception info, as in stdlib logging.
            **fields: Structured fields attached to the record.
        """
        self._emit(level, msg, args, exc_info, fields)

    def trace(self, msg: str, *args: Any, **fields: Any) -> None:
        """Emit a TRACE record (see ``log``)."""
        self._emit(TRACE, msg, args, None, fields)

    def debug(self, msg: str, *args: Any, **fields: Any) -> None:
        """Emit a DEBUG record (see ``log``)."""
        self._emit(logging.DEBUG, msg, args, None, fields)

    def info(self, msg: str, *args: Any, **fields: Any) -> None:
        """Emit an INFO record (see ``log``)."""
        self._emit(logging.INFO, msg, args, None, fields)

    def warning(self, msg: str, *args: Any, **fields: Any) -> None:
        """Emit a WARNING record (see ``log``)."""
        self._emit(logging.WARNING, msg, args, None, fields)

    def error(self, msg: str, *args: Any, **fields: Any) -> None:
        """Emit an ERROR record (see ``log``)."""
        self._emit(logging.ERROR, msg, args, None, fields)

    def critical(self, msg: str, *args: Any, **fields: Any) -> None:
        """Emit a CRITICAL record (see ``log``)."""
        self._emit(logging.CRITICAL, msg, args, None, fields)

    def exception(self, msg: str, *args: Any, **fields: Any) -> None:
        """Emit an ERROR record with the active exception attached."""
        self._emit(logging.ERROR, msg, args, True, fields)


def get_logger(name: str) -> StructuredLogger:
    """Get the logger of a module.

    Libraries call this at import time and never configure logging; only
    entry points call ``setup``.

    Args:
        name: Usually ``__name__``.

    Returns:
        StructuredLogger: The logger for the matching target.
    """
    return StructuredLogger(logging.getLogger(logger_name(target_for(name))))


# endregion
