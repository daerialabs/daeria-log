"""Logging setup for entry points.

Only entry points (a script, an app, a DCC bootstrap) call ``setup``.
Handlers are attached to daeria_log's internal logger only, with propagation
disabled, so the stdlib root logger (and anything a DCC installed on it) is
never touched. Nothing here depends on the project that embeds the package.

Logging must never prevent a tool from starting: invalid levels or
directives and unwritable log directories are reported as warnings in the
log itself instead of raising.

Default log directory: ``<state>/<vendor>/<product>/logs`` (``vendor`` is
``daeria`` unless given, ``product`` is optional).

Environment (``<PREFIX>`` is ``DAERIA`` unless ``env_prefix`` is given):
    <PREFIX>_LOG: Directives as in ``RUST_LOG``, e.g.
        ``info,myproject.bridge=debug,myproject.db=warn``.
    <PREFIX>_LOG_DIR: Overrides the log directory.
"""

# region Imports & Config

# python imports
import atexit
import logging
import os
import queue
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from logging.handlers import QueueListener, RotatingFileHandler
from pathlib import Path
from typing import Any

# daeria_log imports
from daeria_log.capture import install_hooks
from daeria_log.filters import ContextFilter, RedactionFilter
from daeria_log.formatters import ConsoleFormatter, JsonFormatter
from daeria_log.handlers import QueueFront
from daeria_log.levels import parse_level
from daeria_log.logger import INTERNAL_ROOT, get_logger, logger_name

DEFAULT_ENV_PREFIX = "DAERIA"
DEFAULT_VENDOR = "daeria"
MAX_FILE_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 3
QUEUE_SIZE = 10_000
DEFAULT_RETENTION_DAYS = 14
_UNSAFE_SEGMENT = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
LOG_FILE_PATTERN = re.compile(r"^[\w-]+_\d{8}_\d{6}_\d+\.jsonl(\.\d+)?$")

_lock = threading.RLock()
_session_id: str | None = None
# Handlers attached to the loggers (queue front and/or caller's handlers).
_managed_handlers: list[logging.Handler] = []
# Handlers created by setup(), the only ones shutdown() closes.
_owned_handlers: list[logging.Handler] = []
_installed_filters: list[tuple[logging.Handler, logging.Filter]] = []
_overridden_loggers: list[str] = []
_restore_hooks: list[Callable[[], None]] = []
_adopted: list[tuple[logging.Logger, bool]] = []
_queue: "queue.Queue | None" = None
_listener: QueueListener | None = None
_front: QueueFront | None = None
_atexit_registered = False

# endregion

# ==========

# region Directives


def parse_directives(
    text: str, errors: list[str] | None = None
) -> tuple[int | None, dict[str, int]]:
    """Parse ``<PREFIX>_LOG`` style directives.

    Args:
        text: Comma-separated directives. A bare level sets the default,
            ``target=level`` sets one target (the full target name).
        errors: When given, invalid directives are skipped and a message is
            appended here for each of them. When None, the first invalid
            directive raises.

    Returns:
        tuple[int | None, dict[str, int]]: The default level (or None) and
            the per-target levels.

    Raises:
        ValueError: If a directive is invalid and ``errors`` is None.
    """
    default: int | None = None
    targets: dict[str, int] = {}
    for part in filter(None, (chunk.strip() for chunk in text.split(","))):
        try:
            if "=" in part:
                target, _, level = part.partition("=")
                target = target.strip()
                if not target:
                    raise ValueError(f"Empty target in directive {part!r}")
                targets[target] = parse_level(level)
            else:
                default = parse_level(part)
        except ValueError as error:
            if errors is None:
                raise
            errors.append(str(error))
    return default, targets


# endregion

# ==========

# region Setup


def _segment(text: str | None) -> str:
    """Turn a vendor or product name into one safe path segment.

    Args:
        text: Name given by the caller.

    Returns:
        str: The name without path separators or characters that are invalid
            in file names, or an empty string when nothing usable is left.
    """
    return _UNSAFE_SEGMENT.sub("_", text or "").strip(". ")


def default_log_dir(
    env_prefix: str = DEFAULT_ENV_PREFIX,
    vendor: str = DEFAULT_VENDOR,
    product: str | None = None,
) -> Path:
    """Return the per-user log directory for the current platform.

    The layout is ``<platform base>/<vendor>/<product>/logs``, and
    ``<platform base>/<vendor>/logs`` when no product is given. Names are
    used as given, on every platform.

    Args:
        env_prefix: Prefix of the ``<PREFIX>_LOG_DIR`` override variable.
        vendor: Company or team folder (``"daeria"`` by default).
        product: Tool folder (``"qt_modalizer"``...). Omitted when None.

    Returns:
        Path: ``<PREFIX>_LOG_DIR`` if set, otherwise the platform default.
    """
    override = os.environ.get(f"{env_prefix}_LOG_DIR")
    if override:
        return Path(override)
    parts = [_segment(vendor) or DEFAULT_VENDOR]
    if _segment(product):
        parts.append(_segment(product))
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base.joinpath(*parts, "logs")
    if sys.platform == "darwin":
        return Path.home().joinpath("Library", "Logs", *parts)
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return state.joinpath(*parts, "logs")


def is_configured() -> bool:
    """Tell whether ``setup`` is currently in effect.

    Returns:
        bool: True between ``setup`` and ``shutdown``.
    """
    return _session_id is not None


def ensure_setup(app: str, **kwargs: Any) -> str:
    """Configure logging unless it already is.

    Use this from code that may run in a process another tool already
    configured (several tools loaded in the same DCC). The first
    configuration wins; ``kwargs`` are ignored when it already exists.

    Args:
        app: Application name (see ``setup``).
        **kwargs: Other arguments of ``setup``.

    Returns:
        str: The session id, new or existing.
    """
    with _lock:
        if _session_id is not None:
            return _session_id
        return setup(app, **kwargs)


def setup(
    app: str,
    level: str = "info",
    directives: str | None = None,
    log_dir: Path | str | None = None,
    console: bool = True,
    file: bool = True,
    extra_handlers: Iterable[logging.Handler] = (),
    background: bool = True,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    capture_hooks: bool = True,
    adopt_loggers: Iterable[str] = (),
    env_prefix: str = DEFAULT_ENV_PREFIX,
    vendor: str = DEFAULT_VENDOR,
    product: str | None = None,
) -> str:
    """Configure logging for this process, replacing any previous setup.

    Logging is process-global: call this once, from the entry point. Code that
    may run inside a process configured by someone else should call
    ``ensure_setup`` instead.

    Args:
        app: Application name written in every record (``maya``...).
        level: Default level when no directive sets one. An unknown name
            falls back to INFO with a warning.
        directives: Directives string. ``<PREFIX>_LOG`` is applied on top.
            Invalid directives are ignored with a warning.
        log_dir: Directory of the JSONL file. Defaults to
            ``default_log_dir(env_prefix, vendor, product)``.
        console: Write human readable lines to stderr.
        file: Write a rotating JSONL file ``<app>_<date>_<pid>.jsonl``.
        extra_handlers: Additional sinks (Qt bridge, ring buffer...). They are
            called synchronously on the logging thread, keep them cheap. The
            caller owns them: ``shutdown`` detaches and flushes them but does
            not close them.
        background: Write console and file output on a background thread so
            slow storage never blocks the caller (e.g. a DCC's UI thread).
        retention_days: Log files older than this are deleted at startup.
        capture_hooks: Route uncaught exceptions and Python warnings to the
            ``daeria_log.uncaught`` and ``daeria_log.warnings`` targets.
        adopt_loggers: Names of third-party stdlib loggers (e.g.
            ``"sqlalchemy.engine"``) whose records should flow into the
            sinks. Their propagation to the root logger is disabled.
        env_prefix: Prefix of the environment variables read (``<PREFIX>_LOG``
            and ``<PREFIX>_LOG_DIR``).
        vendor: Company or team folder of the default log directory.
        product: Tool folder of the default log directory, e.g.
            ``"qt_modalizer"`` gives ``<state>/daeria/qt_modalizer/logs``.

    Returns:
        str: The session id shared by all records of this run.
    """
    global _queue, _listener, _front, _session_id, _atexit_registered
    with _lock:
        shutdown()
        problems: list[str] = []

        try:
            base = parse_level(level)
        except ValueError as error:
            problems.append(f"{error}; using INFO")
            base = logging.INFO

        env_name = f"{env_prefix}_LOG"
        targets: dict[str, int] = {}
        sources = (
            ("the directives argument", directives),
            (env_name, os.environ.get(env_name)),
        )
        for label, text in sources:
            if not text:
                continue
            errors: list[str] = []
            default, found = parse_directives(text, errors)
            problems.extend(f"Ignored invalid directive from {label}: {e}" for e in errors)
            base = default if default is not None else base
            targets.update(found)

        adopt = list(adopt_loggers)
        session_id = uuid.uuid4().hex[:8]

        sinks: list[logging.Handler] = []
        if console and sys.stderr is not None:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(ConsoleFormatter())
            sinks.append(stream)
        if file:
            handler, error = _make_file_handler(
                app, Path(log_dir) if log_dir else None, env_prefix, vendor, product
            )
            if handler is not None:
                sinks.append(handler)
                _prune_old_logs(Path(handler.baseFilename).parent, retention_days)
            else:
                problems.append(f"File logging disabled: {error}")
        _owned_handlers.extend(sinks)

        attached: list[logging.Handler] = []
        if background and sinks:
            _queue = queue.Queue(maxsize=QUEUE_SIZE)
            _front = QueueFront(_queue)
            _listener = QueueListener(_queue, *sinks, respect_handler_level=True)
            _listener.start()
            attached.append(_front)
            _owned_handlers.append(_front)
        else:
            attached.extend(sinks)
        attached.extend(extra_handlers)

        # Filters run on the producing thread: context and pid must be
        # captured before the record crosses to the writer thread.
        context_filter = ContextFilter(app, session_id)
        redaction_filter = RedactionFilter()
        root = logging.getLogger(INTERNAL_ROOT)
        for handler in attached:
            for log_filter in (context_filter, redaction_filter):
                handler.addFilter(log_filter)
                _installed_filters.append((handler, log_filter))
            root.addHandler(handler)
            _managed_handlers.append(handler)

        root.setLevel(base)
        root.propagate = False
        for target, target_level in targets.items():
            names = [logger_name(target)]
            if target in adopt:
                names.append(target)
            for name in names:
                logging.getLogger(name).setLevel(target_level)
                _overridden_loggers.append(name)

        for name in adopt:
            adopted = logging.getLogger(name)
            _adopted.append((adopted, adopted.propagate))
            adopted.propagate = False
            for handler in attached:
                adopted.addHandler(handler)

        if capture_hooks:
            _restore_hooks.extend(install_hooks())

        if not _atexit_registered:
            atexit.register(shutdown)
            _atexit_registered = True

        _session_id = session_id
        log = get_logger("daeria_log")
        for problem in problems:
            log.warning("%s", problem)
        return session_id


def flush() -> None:
    """Wait until every queued record has been written."""
    if _queue is not None and _listener is not None:
        _queue.join()
    for handler in _managed_handlers + _owned_handlers:
        handler.flush()


def dropped_count() -> int:
    """Return the number of records dropped because the queue was full.

    Returns:
        int: Dropped records since ``setup``.
    """
    return _front.dropped if _front is not None else 0


def shutdown() -> None:
    """Flush, then undo everything installed by ``setup``.

    Handlers and filters are detached again. Handlers created by ``setup``
    are closed; handlers passed in ``extra_handlers`` belong to the caller
    and are only flushed.
    """
    global _queue, _listener, _front, _session_id
    with _lock:
        for restore in reversed(_restore_hooks):
            restore()
        _restore_hooks.clear()

        root = logging.getLogger(INTERNAL_ROOT)
        for adopted, propagate in _adopted:
            for handler in _managed_handlers:
                adopted.removeHandler(handler)
            adopted.propagate = propagate
        _adopted.clear()

        for handler in _managed_handlers:
            root.removeHandler(handler)
        if _listener is not None:
            _listener.stop()  # drains the queue before returning
        for handler, log_filter in _installed_filters:
            handler.removeFilter(log_filter)
        _installed_filters.clear()

        for handler in _managed_handlers:
            if handler not in _owned_handlers:
                handler.flush()
        for handler in _owned_handlers:
            handler.close()
        _managed_handlers.clear()
        _owned_handlers.clear()
        _queue = _listener = _front = None
        _session_id = None

        for name in _overridden_loggers:
            logging.getLogger(name).setLevel(logging.NOTSET)
        _overridden_loggers.clear()


def _make_file_handler(
    app: str,
    directory: Path | None,
    env_prefix: str,
    vendor: str,
    product: str | None,
) -> tuple[logging.Handler | None, str | None]:
    """Create the rotating JSONL handler.

    Args:
        app: Application name used in the file name.
        directory: Target directory, or None for the default.
        env_prefix: Prefix used to resolve the default directory.
        vendor: Vendor folder of the default directory.
        product: Product folder of the default directory.

    Returns:
        tuple[logging.Handler | None, str | None]: The handler, or None and
            the reason when the directory is not writable (logging must never
            prevent the tool from starting).
    """
    directory = directory or default_log_dir(env_prefix, vendor, product)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    path = directory / f"{app}_{stamp}_{os.getpid()}.jsonl"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=MAX_FILE_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
    except OSError as error:
        return None, f"{directory}: {error}"
    handler.setFormatter(JsonFormatter())
    return handler, None


def _prune_old_logs(directory: Path, retention_days: int) -> None:
    """Delete log files older than the retention period.

    Only files matching the daeria_log naming scheme are touched. Files that
    cannot be removed (still open in another process) are skipped, and so is
    a directory that cannot be listed.

    Args:
        directory: Log directory.
        retention_days: Age in days beyond which files are removed.
    """
    if retention_days <= 0:
        return
    limit = time.time() - retention_days * 86400
    try:
        candidates = list(directory.iterdir())
    except OSError:
        return
    for path in candidates:
        if not LOG_FILE_PATTERN.match(path.name):
            continue
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
        except OSError:
            continue


# endregion
