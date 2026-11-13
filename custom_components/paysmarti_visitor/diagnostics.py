"""Safe structural diagnostics for form failures."""

import json
from pathlib import Path

_VERSION = json.loads(Path(__file__).with_name("manifest.json").read_text(encoding="utf-8"))["version"]


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    """Expose a fixed set of metadata fields; never inspect credentials or HTML."""
    error = entry.runtime_data.last_error or {}
    return {
        "version": _VERSION,
        "stage": error.get("stage"),
        "code": error.get("code"),
        "http_status": error.get("http_status"),
        "path": error.get("path"),
        "field_names": error.get("field_names", []),
    }
