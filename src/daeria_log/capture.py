"""Capture of events that bypass normal logging.

Routes uncaught exceptions (main thread and worker threads) and Python
warnings into daeria_log. Previous hooks are chained or restored,
so a DCC's own handling keeps working.
"""

# region Imports & Config

# python imports
import logging
import sys
import threading
import warnings
from collections.abc import Callable
from typing import Any

# daeria_log imports
from daeria_log.logger import get_logger

UNCAUGHT_TARGET = "daeria_log.uncaught"
WARNINGS_TARGET = "daeria_log.warnings"

# endregion

# ==========

# region Hooks


def install_hooks() -> list[Callable[[], None]]:
    """Install the exception and warning hooks.

    Returns:
        list[Callable[[], None]]: Functions restoring the previous hooks.
    """
    uncaught = get_logger(UNCAUGHT_TARGET)
    warned = get_logger(WARNINGS_TARGET)

    previous_excepthook = sys.excepthook
    previous_thread_hook = threading.excepthook
    previous_showwarning = warnings.showwarning

    def excepthook(exc_type: Any, exc: Any, tb: Any) -> None:
        if not issubclass(exc_type, KeyboardInterrupt):
            uncaught.log(
                logging.CRITICAL, "Uncaught exception", exc_info=(exc_type, exc, tb)
            )
        previous_excepthook(exc_type, exc, tb)

    def thread_hook(args: Any) -> None:
        if args.exc_type is not SystemExit:
            name = args.thread.name if args.thread else "unknown"
            uncaught.log(
                logging.CRITICAL,
                "Uncaught exception in thread",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                thread=name,
            )
        previous_thread_hook(args)

    def showwarning(message: Any, category: Any, filename: str, lineno: int, *_: Any):
        warned.warning(
            "%s", message, category=category.__name__, file=filename, line=lineno
        )

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    warnings.showwarning = showwarning

    def restore() -> None:
        sys.excepthook = previous_excepthook
        threading.excepthook = previous_thread_hook
        warnings.showwarning = previous_showwarning

    return [restore]


# endregion
