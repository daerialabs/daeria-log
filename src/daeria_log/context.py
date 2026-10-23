"""Ambient logging context.

Fields bound here (e.g. ``publish_id``, ``correlation_id``) are attached to
every record emitted inside the scope, like a tracing span. Built on
``contextvars``: scopes follow asyncio tasks, but not plain threads.
"""

# region Imports & Config

# python imports
import functools
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "daeria_log_context", default=None
)

# endregion

# ==========

# region Context API


def current_context() -> dict[str, Any]:
    """Return a copy of the fields bound in the current scope.

    Returns:
        dict[str, Any]: The bound fields.
    """
    return dict(_CONTEXT.get() or {})


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Bind fields to every record emitted inside the ``with`` block.

    Scopes nest: inner fields extend or override outer ones.

    Args:
        **fields: Fields to bind.

    Yields:
        None: Control to the body of the ``with`` block.
    """
    token = _CONTEXT.set({**(_CONTEXT.get() or {}), **fields})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def new_correlation_id() -> str:
    """Create a short unique correlation id.

    Returns:
        str: A 12-character hexadecimal id.
    """
    return uuid.uuid4().hex[:12]


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Iterator[str]:
    """Bind a correlation id that ties together one logical operation.

    Args:
        correlation_id: Id to reuse (e.g. received from another process).
            A new one is generated when omitted.

    Yields:
        str: The correlation id in effect.
    """
    cid = correlation_id or new_correlation_id()
    with log_context(correlation_id=cid):
        yield cid


def wrap_with_context(func: Callable[..., Any]) -> Callable[..., Any]:
    """Make a function run with the context bound *now*.

    Use it for callables handed to threads or Qt workers, which do not
    inherit ``contextvars``.

    Args:
        func: The function to wrap.

    Returns:
        Callable: A function that restores the captured context when called.
    """
    captured = current_context()

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with log_context(**captured):
            return func(*args, **kwargs)

    return wrapper


class ContextThread(threading.Thread):
    """Thread that runs inside the logging context of its creator."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the thread, capturing the current context.

        Args:
            *args: Forwarded to ``threading.Thread``.
            **kwargs: Forwarded to ``threading.Thread``.
        """
        super().__init__(*args, **kwargs)
        self._captured_context = current_context()

    def run(self) -> None:
        """Run the thread body inside the captured context."""
        with log_context(**self._captured_context):
            super().run()


# endregion
