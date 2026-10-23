"""Tests for daeria_log."""

# region Imports & Config

# python imports
import json
import logging
import sys
from pathlib import Path

# third-party imports
import pytest

# daeria_log imports
import daeria_log
from daeria_log import (
    RingBufferHandler,
    correlation_scope,
    default_log_dir,
    ensure_setup,
    get_logger,
    is_configured,
    log_context,
    parse_directives,
    setup,
    shutdown,
)
from daeria_log.filters import is_sensitive_key, redact_fields, redact_text
from daeria_log.logger import TARGET_ALIASES, register_alias, target_for

# endregion

# ==========

# region Fixtures


@pytest.fixture(autouse=True)
def _clean_logging():
    """Reset logging around each test."""
    shutdown()
    yield
    shutdown()


class _TrackedBuffer(RingBufferHandler):
    """Ring buffer that remembers whether it was closed."""

    was_closed = False

    def close(self):
        """Record the close, then close normally."""
        self.was_closed = True
        super().close()


# endregion

# ==========

# region Tests


def test_target_mapping():
    """Targets are the caller's names; only registered aliases are remapped."""
    assert target_for("myproject.core.publisher") == "myproject.core.publisher"
    assert target_for("something") == "something"
    assert target_for("__main__") == "main"
    assert target_for("") == "root"
    register_alias("my_ui", "myproject.ui")
    try:
        assert target_for("my_ui.widgets.toolbar") == "myproject.ui.widgets.toolbar"
    finally:
        TARGET_ALIASES.pop("my_ui")


def test_directives():
    """Directives are parsed."""
    default, targets = parse_directives("warn, bridge=debug,myproject.db=off")
    assert default == logging.WARNING
    assert targets == {"bridge": logging.DEBUG, "myproject.db": 100}
    with pytest.raises(ValueError):
        parse_directives("loud")


def test_directives_collect_errors_instead_of_raising():
    """With an error list, valid parts survive and bad ones are reported."""
    errors: list[str] = []
    default, targets = parse_directives("loud,debug,x=nope,=info,y=warn", errors)
    assert default == logging.DEBUG
    assert targets == {"y": logging.WARNING}
    assert len(errors) == 3


def test_json_file_and_fields(tmp_path):
    """Records land in a JSONL file with fields and correlation id."""
    setup("test", log_dir=tmp_path, console=False)
    log = get_logger("myproject.core.publisher")
    with correlation_scope("abc123"), log_context(publish_id=7):
        log.info("Published %s", "hero", version=3)
    shutdown()

    (path,) = tmp_path.glob("test_*.jsonl")
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["message"] == "Published hero"
    assert record["target"] == "myproject.core.publisher"
    assert record["fields"] == {"version": 3}
    assert record["correlation_id"] == "abc123"
    assert record["context"] == {"publish_id": 7}
    assert record["app"] == "test"
    assert record["ts"].endswith("Z")
    assert record["caller"].startswith("test_daeria_log.py:")


def test_per_target_levels():
    """A target directive lowers the threshold for that target only."""
    buffer = RingBufferHandler()
    setup(
        "test",
        directives="warn,myproject.bridge=debug",
        console=False,
        file=False,
        extra_handlers=[buffer],
    )
    get_logger("myproject.bridge.link").debug("noisy")
    get_logger("myproject.core.x").info("hidden")
    get_logger("myproject.core.x").warning("shown")
    assert [r["message"] for r in buffer.records()] == ["noisy", "shown"]


def test_redaction():
    """Secrets never reach the sinks."""
    buffer = RingBufferHandler()
    setup("test", console=False, file=False, extra_handlers=[buffer])
    get_logger("myproject.db").info(
        "Connecting to postgresql://app:hunter2@db/app", password="hunter2"
    )
    (record,) = buffer.records()
    assert "hunter2" not in json.dumps(record)
    assert record["fields"]["password"] == "***"


def test_library_does_not_touch_root_logger(tmp_path):
    """Setup only installs handlers on daeria_log's internal logger."""
    root_handlers = list(logging.getLogger().handlers)
    setup("test", log_dir=tmp_path, console=False)
    assert logging.getLogger().handlers == root_handlers
    assert logging.getLogger(daeria_log.INTERNAL_ROOT).propagate is False


def test_setup_is_idempotent(tmp_path):
    """Calling setup twice does not duplicate handlers."""
    buffer = RingBufferHandler()
    setup("test", console=False, file=False, extra_handlers=[buffer])
    setup("test", console=False, file=False, extra_handlers=[buffer])
    get_logger("myproject.x").info("once")
    assert len(buffer.records()) == 1


def test_exception_is_captured():
    """exception() attaches the traceback."""
    buffer = RingBufferHandler()
    setup("test", console=False, file=False, extra_handlers=[buffer])
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        get_logger("myproject.x").exception("failed %s", "task")
    (record,) = buffer.records()
    assert "RuntimeError: boom" in record["exception"]
    assert daeria_log.TRACE == 5


# endregion

# ==========

# region Setup robustness tests


def test_invalid_env_directive_does_not_crash(monkeypatch):
    """A typo in DAERIA_LOG is reported, valid parts still apply."""
    monkeypatch.setenv("DAERIA_LOG", "infoo,debug")
    buffer = RingBufferHandler()
    setup("test", console=False, file=False, extra_handlers=[buffer])
    get_logger("myproject.x").debug("visible")
    warning, debug = buffer.records()
    assert warning["level"] == "WARNING"
    assert "DAERIA_LOG" in warning["message"]
    assert debug["message"] == "visible"


def test_invalid_level_argument_falls_back_to_info():
    """An unknown level argument does not raise."""
    buffer = RingBufferHandler()
    setup("test", level="loud", console=False, file=False, extra_handlers=[buffer])
    get_logger("myproject.x").debug("hidden")
    get_logger("myproject.x").info("shown")
    warning, shown = buffer.records()
    assert "Unknown log level" in warning["message"]
    assert shown["message"] == "shown"


def test_env_prefix_is_configurable(tmp_path, monkeypatch):
    """A host project can use its own environment variable names."""
    monkeypatch.setenv("MYTOOL_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MYTOOL_LOG", "warn")
    buffer = RingBufferHandler()
    setup("test", console=False, extra_handlers=[buffer], env_prefix="MYTOOL")
    get_logger("myproject.x").info("hidden")
    get_logger("myproject.x").warning("shown")
    shutdown()
    assert list(tmp_path.glob("test_*.jsonl"))
    assert [r["message"] for r in buffer.records()] == ["shown"]


def test_ensure_setup_keeps_first_configuration():
    """ensure_setup does not replace an existing configuration."""
    first = RingBufferHandler()
    second = RingBufferHandler()
    assert not is_configured()
    session = setup("toolA", console=False, file=False, extra_handlers=[first])
    assert is_configured()
    assert ensure_setup("toolB", console=False, file=False, extra_handlers=[second]) == session
    get_logger("myproject.x").info("hi")
    assert [r["app"] for r in first.records()] == ["toolA"]
    assert second.records() == []
    shutdown()
    assert not is_configured()
    assert ensure_setup("toolB", console=False, file=False) != session


def test_shutdown_removes_filters_and_does_not_close_caller_handlers():
    """Reusing a handler across setups neither piles up filters nor closes it."""
    buffer = _TrackedBuffer()
    for _ in range(3):
        setup("test", console=False, file=False, extra_handlers=[buffer])
        assert len(buffer.filters) == 2
        shutdown()
        assert buffer.filters == []
    assert not buffer.was_closed


def test_unwritable_directory_warns(tmp_path):
    """A bad log directory is reported instead of failing silently."""
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    buffer = RingBufferHandler()
    setup("test", log_dir=blocker, console=False, extra_handlers=[buffer])
    (record,) = buffer.records()
    assert record["level"] == "WARNING"
    assert "File logging disabled" in record["message"]


# endregion

# ==========

# region Log directory tests


@pytest.fixture
def _linux_state(tmp_path, monkeypatch):
    """Pretend to be on Linux with a temporary XDG state directory."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("DAERIA_LOG_DIR", raising=False)
    return tmp_path


def test_default_log_dir_uses_vendor_and_product(_linux_state):
    """The directory follows <state>/<vendor>/<product>/logs."""
    state = _linux_state
    assert default_log_dir() == state / "daeria" / "logs"
    assert default_log_dir(product="qt_modalizer") == state / "daeria" / "qt_modalizer" / "logs"
    assert default_log_dir(vendor="acme", product="tool") == state / "acme" / "tool" / "logs"


def test_log_dir_names_cannot_escape_the_base(_linux_state):
    """Separators and dot segments in names are neutralised."""
    state = _linux_state
    assert default_log_dir(product="../../etc") == state / "daeria" / "_.._etc" / "logs"
    assert default_log_dir(product="..") == state / "daeria" / "logs"
    assert default_log_dir(vendor="") == state / "daeria" / "logs"


def test_log_dir_env_override_wins(_linux_state, monkeypatch):
    """<PREFIX>_LOG_DIR beats vendor and product."""
    monkeypatch.setenv("DAERIA_LOG_DIR", "/somewhere/else")
    assert default_log_dir(vendor="acme", product="tool") == Path("/somewhere/else")


def test_windows_layout(tmp_path, monkeypatch):
    """On Windows the base is LOCALAPPDATA."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("DAERIA_LOG_DIR", raising=False)
    assert default_log_dir(product="qt_modalizer") == tmp_path / "daeria" / "qt_modalizer" / "logs"


def test_setup_writes_to_product_directory(_linux_state):
    """setup(vendor=..., product=...) selects the default log directory."""
    setup("test", console=False, vendor="acme", product="qt_modalizer")
    get_logger("myproject.x").info("hi")
    shutdown()
    folder = _linux_state / "acme" / "qt_modalizer" / "logs"
    assert len(list(folder.glob("test_*.jsonl"))) == 1


# endregion

# ==========

# region Redaction tests


@pytest.mark.parametrize(
    "key",
    ["password", "db_password", "apiKey", "API_KEY", "x-api-key", "accessToken",
     "client_secret", "Authorization", "private_key"],
)
def test_sensitive_keys_are_detected(key):
    """Whole-word matching catches the usual secret names."""
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key",
    ["max_tokens", "passenger_count", "compass", "pass_name", "render_pass",
     "bypass", "keyframe", "author"],
)
def test_harmless_keys_are_kept(key):
    """Substring look-alikes are not masked."""
    assert not is_sensitive_key(key)
    assert redact_fields({key: "value"}) == {key: "value"}


def test_redact_text_masks_key_value_pairs_in_messages():
    """Secrets written inside free text are masked."""
    assert redact_text("login password=hunter2 ok") == "login password=*** ok"
    assert redact_text("GET /x?token=abc123&page=2") == "GET /x?token=***&page=2"
    assert redact_text('{"api_key": "abc 123"}') == '{"api_key": ***}'
    assert redact_text("Authorization: Bearer abc.def") == "Authorization: ***"
    assert redact_text("max_tokens=100 at 12:30:45") == "max_tokens=100 at 12:30:45"


def test_message_secret_never_reaches_sinks():
    """The filter scrubs the message, not only the fields."""
    buffer = RingBufferHandler()
    setup("test", console=False, file=False, extra_handlers=[buffer])
    get_logger("myproject.x").info("login password=%s ok", "hunter2")
    assert "hunter2" not in json.dumps(buffer.records()[0])


# endregion

# ==========

# region Robustness tests


def test_schema_version(tmp_path):
    """Every record carries the schema version."""
    buffer = RingBufferHandler()
    setup("test", console=False, file=False, extra_handlers=[buffer])
    get_logger("myproject.x").info("hi")
    assert buffer.records()[0]["schema"] == 1


def test_direct_log_reports_real_caller():
    """log() and info() both report the calling line, not the wrapper."""
    buffer = RingBufferHandler()
    setup("test", console=False, file=False, extra_handlers=[buffer])
    log = get_logger("myproject.x")
    log.log(logging.INFO, "direct")
    log.info("shortcut")
    assert all(r["caller"].startswith("test_daeria_log.py:") for r in buffer.records())


def test_context_follows_threads():
    """ContextThread and wrap_with_context carry the logging context."""
    buffer = RingBufferHandler()
    setup("test", console=False, file=False, extra_handlers=[buffer])
    log = get_logger("myproject.x")
    with correlation_scope("cid42"):
        thread = daeria_log.ContextThread(target=lambda: log.info("in thread"))
        thread.start()
        thread.join()
    assert buffer.records()[0]["correlation_id"] == "cid42"


def test_background_writer_flushes_on_shutdown(tmp_path):
    """Queued records all reach the file once shutdown drains the queue."""
    setup("test", log_dir=tmp_path, console=False, background=True)
    log = get_logger("myproject.x")
    for index in range(500):
        log.info("record %d", index)
    shutdown()
    (path,) = tmp_path.glob("test_*.jsonl")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 500


def test_retention_prunes_only_old_log_files(tmp_path):
    """Old daeria_log files are removed; unrelated files are left alone."""
    import os
    import time

    old = tmp_path / "maya_20200101_000000_1.jsonl"
    keep = tmp_path / "notes.jsonl"
    old.write_text("x")
    keep.write_text("x")
    past = time.time() - 40 * 86400
    os.utime(old, (past, past))
    os.utime(keep, (past, past))
    setup("test", log_dir=tmp_path, console=False, retention_days=14)
    assert not old.exists()
    assert keep.exists()


def test_uncaught_exception_is_logged_and_chained(monkeypatch):
    """sys.excepthook logs the failure and still calls the previous hook."""
    import sys

    previous_calls = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: previous_calls.append(args))
    buffer = RingBufferHandler()
    setup("test", console=False, file=False, extra_handlers=[buffer])
    try:
        raise ValueError("kaboom")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    record = buffer.records()[0]
    assert record["target"] == "daeria_log.uncaught"
    assert "kaboom" in record["exception"]
    assert previous_calls and previous_calls[0][0] is ValueError  # chained
    shutdown()


def test_warnings_are_captured():
    """Python warnings flow into daeria_log.warnings."""
    import warnings

    buffer = RingBufferHandler()
    setup("test", console=False, file=False, extra_handlers=[buffer])
    warnings.warn("careful", UserWarning, stacklevel=1)
    assert buffer.records()[0]["target"] == "daeria_log.warnings"


def test_adopted_third_party_logger_and_nested_redaction():
    """Adopted loggers reach the sinks, and nested secrets are masked."""
    buffer = RingBufferHandler()
    setup(
        "test",
        console=False,
        file=False,
        extra_handlers=[buffer],
        adopt_loggers=["thirdparty.lib"],
    )
    logging.getLogger("thirdparty.lib").warning("hello")
    get_logger("myproject.x").info("cfg", conf={"db": {"password": "p4ss"}})
    first, second = buffer.records()
    assert first["message"] == "hello"
    assert first["target"] == "thirdparty.lib"
    assert "p4ss" not in json.dumps(second)


# endregion
