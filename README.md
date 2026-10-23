# daeria-log

Structured logging for Python tools, scripts and DCC pipelines. Standard
library only, one JSON record format everywhere.

- **One format**: every tool writes the same JSON lines (see `SCHEMA.md`),
  so logs from a script, an app and a DCC can be read and merged together.
- **Safe to embed**: never touches the stdlib root logger, never blocks the
  caller (writing happens on a background thread) and never prevents a tool
  from starting, even with a bad config or an unwritable folder.
- **Context that follows the work**: bind fields or a correlation id once
  and every record inside the scope carries them, threads included.
- **Secrets masked** before anything is written.
- **Zero dependencies**, drop-in for any repo.

## Install

Requires Python 3.11+.

```
pip install git+https://github.com/daerialabs/daeria-log.git
```

Inside a DCC, use the interpreter of the application (for example
`mayapy -m pip install ...`), or add the `src` folder to its `PYTHONPATH`.

## Quick start

Libraries and modules only ask for a logger. They never configure anything:

```python
from daeria_log import get_logger

log = get_logger(__name__)
log.info("Published %s", asset, version=3)  # keyword args become fields
log.exception("Export failed")              # attaches the traceback
```

The entry point (script, app, DCC bootstrap) configures logging **once per
process**:

```python
import daeria_log

daeria_log.setup(app="maya")
```

`setup()` replaces any previous configuration. When several tools may start
in the same process, use `ensure_setup()`: it does nothing if logging is
already configured and returns the existing session id.

| Argument | Meaning |
|---|---|
| `app` | The running process: `"maya"`, `"houdini"`, `"cli"`. Used in the file name and in every record. |
| `product` | Tool or pipeline folder of the log directory. |
| `vendor` | Company or team folder of the log directory (default `daeria`). |
| `level` | Default level (default `info`). |
| `console`, `file` | Enable the console and JSONL file sinks. |
| `extra_handlers` | Your own sinks, such as a UI panel (see below). |
| `capture_hooks` | Route uncaught exceptions and Python warnings into the log. |
| `adopt_loggers` | Third-party stdlib loggers to route into the same sinks. |
| `env_prefix` | Prefix of the environment variables (default `DAERIA`). |

## Context

Fields bound with `log_context` and ids bound with `correlation_scope` are
attached to every record emitted inside the block:

```python
from daeria_log import correlation_scope, log_context

with correlation_scope(), log_context(shot="sh010"):
    log.info("Exporting")  # carries correlation_id and shot
```

Threads do not inherit the context on their own. Use `ContextThread`, or
`wrap_with_context(func)` for callables handed to a Qt worker or a DCC's
main-thread queue:

```python
from daeria_log import ContextThread, wrap_with_context

ContextThread(target=work).start()
queue_in_main_thread(wrap_with_context(work))
```

A correlation id can also be received from another process (an app that
launches a DCC, for instance) with `correlation_scope(received_id)`, which
ties the logs of both sides together.

## Targets

A record's `target` is the name given to `get_logger`, so `__name__` is
enough. `register_alias("my_pkg", "myproject.pkg")` remaps a top-level
package when module names are not the target names you want.

Handlers are attached to an internal logger that sits above every target,
so the stdlib root logger, and anything a host application installed on it,
is left alone. Third-party stdlib loggers can be routed into the same sinks
with `setup(adopt_loggers=["sqlalchemy.engine"])`.

## Log files

Files go to `<state>/<vendor>/<product>/logs`, where `<state>` is
`~/.local/state` on Linux, `%LOCALAPPDATA%` on Windows and
`~/Library/Logs` on macOS:

```python
daeria_log.setup(app="maya")
# Linux: ~/.local/state/daeria/logs
daeria_log.setup(app="maya", product="my_tool")
# Linux: ~/.local/state/daeria/my_tool/logs
daeria_log.setup(app="maya", vendor="acme", product="rigger")
# Linux: ~/.local/state/acme/rigger/logs
```

`log_dir=` or `<PREFIX>_LOG_DIR` override the whole path.

- One file per process: `<app>_<date>_<pid>.jsonl`, so processes never fight
  over rotation.
- Rotation at 10 MB (3 backups per file), files older than 14 days are
  deleted at startup (`retention_days=`).
- If the folder is not writable, file logging is disabled and a warning is
  logged. The tool keeps running.

## Levels and environment

Levels are `trace`, `debug`, `info`, `warn`, `error`, `critical` and `off`.
The prefix of the variables below defaults to `DAERIA` and can be changed
with `setup(env_prefix="MYTOOL")`.

| Variable | Effect |
|---|---|
| `<PREFIX>_LOG` | Directives as in `RUST_LOG`, with full target names: `info,myproject.db=warn,myproject.bridge=debug`. Invalid parts are ignored with a warning. |
| `<PREFIX>_LOG_DIR` | Overrides the log directory. |

```
DAERIA_LOG=debug,myproject.db=off python my_script.py
```

## Using it in a DCC

The pattern is the same as anywhere else: one `setup` in the bootstrap of
the application, and only `get_logger(__name__)` in the tools.

```python
# DCC startup script, runs once per session
import daeria_log

daeria_log.ensure_setup(app="maya", product="my_pipeline")
```

Points to keep in mind:

- **Global hooks.** `capture_hooks=True` replaces `sys.excepthook` and
  `warnings.showwarning` for the whole process (previous hooks are chained
  or restored). If the host application or other plugins rely on them, pass
  `capture_hooks=False`.
- **Console.** Console lines are written to `sys.stderr` from a background
  thread. If the host redirects stderr, check the behavior or pass
  `console=False`.
- **Threads.** Host APIs usually only work on the main thread. Never call
  them from a log callback.

### Showing logs in a UI panel

`RingBufferHandler` keeps the last records in memory, and `CallbackHandler`
calls a function with each record as a dict. Callbacks run on the thread
that logged, so a UI must hand the record over (for example through a Qt
signal) instead of touching widgets directly:

```python
from daeria_log import CallbackHandler, RingBufferHandler

buffer = RingBufferHandler(capacity=2000)
daeria_log.setup(app="maya", extra_handlers=[buffer])

buffer.records()  # the last records, oldest first
```

Handlers passed in `extra_handlers` belong to you: `shutdown()` detaches
and flushes them but does not close them.

## Secrets

Before a record is written, secrets are masked as `***`:

- field names that contain a sensitive word (`password`, `token`, `secret`,
  `api_key`, `authorization`...), matched as whole words, so `max_tokens` or
  `pass_name` are left alone
- `key=value` pairs inside messages (`password=...`, `?token=...`,
  `Authorization: Bearer ...`)
- the password of `scheme://user:password@host` URLs

Masking is best effort. Command line flags written as `--password value`
are not detected, so avoid logging raw secrets in the first place.

## Other helpers

- `flush()` waits until queued records are written, `shutdown()` drains the
  queue and removes everything `setup()` installed (it also runs at exit).
- `dropped_count()` reports records dropped because the queue was full.
- `TRACE` is a level below `DEBUG`.

## Development

```
pip install pytest
pytest
```

With [uv](https://docs.astral.sh/uv/): `uv run --with pytest pytest`.

## License

Apache License 2.0, see `LICENSE`.

Copyright 2026 Clement Daures