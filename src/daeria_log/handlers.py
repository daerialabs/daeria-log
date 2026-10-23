"""Extra handlers: callbacks and an in-memory ring buffer.

These are the building blocks for UI sinks (log panel, "View Logs") and for
forwarding sinks.
"""

# region Imports & Config

# python imports
import copy
import logging
import queue
from collections import deque
from collections.abc import Callable
from logging.handlers import QueueHandler
from typing import Any

# daeria_log imports
from daeria_log.formatters import record_to_dict

LogDict = dict[str, Any]

# endregion

# ==========

# region Handlers


class CallbackHandler(logging.Handler):
    """Call a function with the structured dict of every record."""

    def __init__(self, callback: Callable[[LogDict], None], level: int = 1) -> None:
        """Initialize the handler.

        Args:
            callback: Called with the record dict. It may run on any thread.
            level: Minimum level handled.
        """
        super().__init__(level)
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        """Forward a record to the callback.

        Args:
            record: The record to forward.
        """
        try:
            self._callback(record_to_dict(record))
        except Exception:
            self.handleError(record)


class RingBufferHandler(CallbackHandler):
    """Keep the last N records in memory."""

    def __init__(self, capacity: int = 2000, level: int = 1) -> None:
        """Initialize the buffer.

        Args:
            capacity: Maximum number of records kept.
            level: Minimum level handled.
        """
        self._buffer: deque[LogDict] = deque(maxlen=capacity)
        super().__init__(self._buffer.append, level)

    def records(self) -> list[LogDict]:
        """Return the buffered records, oldest first.

        Returns:
            list[dict]: A snapshot of the buffer.
        """
        return list(self._buffer)


class QueueFront(QueueHandler):
    """Hand records to a background writer without blocking the caller.

    Unlike the stdlib prepare step, the structure of the record is kept
    (exception info, fields), only the message is resolved. When the queue
    is full, records are dropped and counted rather than blocking the DCC.
    """

    def __init__(self, record_queue: "queue.Queue[logging.LogRecord]") -> None:
        """Initialize the handler.

        Args:
            record_queue: Queue drained by a ``QueueListener``.
        """
        super().__init__(record_queue)
        self.dropped = 0

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        """Detach the record from its arguments.

        Args:
            record: The record to enqueue.

        Returns:
            logging.LogRecord: A copy with the message resolved.
        """
        clone = copy.copy(record)
        clone.msg = record.getMessage()
        clone.args = ()
        return clone

    def enqueue(self, record: logging.LogRecord) -> None:
        """Queue a record, dropping it if the queue is full.

        Args:
            record: The record to enqueue.
        """
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1


# endregion
