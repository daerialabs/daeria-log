# Daeria log record schema (v0.1.0)

One JSON object per line in `<app>_<YYYYmmdd_HHMMSS>_<pid>.jsonl`.

| Field | Type | Notes |
|---|---|---|
| `schema` | int | Schema version (`1`). Bumped on breaking changes. |
| `ts` | string | UTC, ISO 8601 with milliseconds, `Z` suffix. |
| `level` | string | `TRACE` `DEBUG` `INFO` `WARNING` `ERROR` `CRITICAL`. |
| `target` | string | Dotted logger name chosen by the emitting code (e.g. `myproject.core.publisher`). |
| `message` | string | Final, formatted message. |
| `fields` | object | Structured keyword fields of the call. |
| `app` | string | `maya`, `houdini`, `desktop`... |
| `pid` | int | Process id. |
| `host` | string | Machine name. |
| `session_id` | string | Shared by all records of one process run. |
| `correlation_id` | string/null | Ties one logical operation together. |
| `context` | object | Other fields bound with `log_context`. |
| `caller` | string | `file.py:line`. |
| `exception` | string | Present only when an exception is attached. |

Secrets are masked (`***`) before writing. Readers must ignore unknown fields.
