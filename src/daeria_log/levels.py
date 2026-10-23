"""Log levels for daeria_log.

Adds a ``TRACE`` level below ``DEBUG`` (as in Rust's ``tracing``) and parses
the level names used in log directives.
"""

# region Imports & Config

# python imports
import logging

TRACE = 5
OFF = 100

logging.addLevelName(TRACE, "TRACE")

_LEVELS = {
    "trace": TRACE,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
    "off": OFF,
}

# endregion

# ==========

# region Parsing


def parse_level(name: str) -> int:
    """Convert a level name to its numeric value.

    Args:
        name: Case-insensitive level name (``trace`` ... ``critical``, ``off``).

    Returns:
        int: The numeric logging level.

    Raises:
        ValueError: If the name is not a known level.
    """
    try:
        return _LEVELS[name.strip().lower()]
    except KeyError:
        raise ValueError(f"Unknown log level: {name!r}") from None


# endregion
