"""Safe structural diagnostics for form failures."""

import json
from pathlib import Path

_VERSION = json.loads(Path(__file__).with_name("manifest.json").read_text(encoding="utf-8"))["version"]


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    """Expose a fixed set of metadata fields; never inspect credentials or HTML."""
    error = entry.runtime_data.last_error or {}
    journal = getattr(entry.runtime_data, "journal", None)
    attempt = await journal.last() if journal is not None else None
    status = getattr(attempt, "status", None)
    if status not in {"preparing", "commit_started", "booked", "not_booked", "outcome_unknown"}:
        status = None
    return {
        "version": _VERSION,
        "stage": error.get("stage"),
        "code": error.get("code"),
        "http_status": error.get("http_status"),
        "path": error.get("path"),
        "field_names": error.get("field_names", []),
        "last_attempt_status": status,
    }
