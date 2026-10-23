"""Daeria Log: unified, structured logging for Daeria tools.

Standard library only, embeddable in any project. Libraries call
``get_logger(__name__)``; entry points call ``setup(app=...)`` once (or
``ensure_setup`` when the process may already be configured).
"""

# region Imports & Config

# python imports
import logging

# daeria_log imports
from daeria_log.config import (
    default_log_dir,
    dropped_count,
    ensure_setup,
    flush,
    is_configured,
    parse_directives,
    setup,
    shutdown,
)
from daeria_log.context import (
    ContextThread,
    correlation_scope,
    current_context,
    log_context,
    new_correlation_id,
    wrap_with_context,
)
from daeria_log.handlers import CallbackHandler, RingBufferHandler
from daeria_log.levels import TRACE
from daeria_log.logger import INTERNAL_ROOT, StructuredLogger, get_logger, register_alias

# A library must stay silent until an entry point configures logging.
logging.getLogger(INTERNAL_ROOT).addHandler(logging.NullHandler())

__all__ = [
    "TRACE",
    "ContextThread",
    "CallbackHandler",
    "RingBufferHandler",
    "StructuredLogger",
    "correlation_scope",
    "current_context",
    "default_log_dir",
    "dropped_count",
    "ensure_setup",
    "flush",
    "get_logger",
    "is_configured",
    "log_context",
    "new_correlation_id",
    "parse_directives",
    "register_alias",
    "setup",
    "shutdown",
    "wrap_with_context",
]

# endregion
