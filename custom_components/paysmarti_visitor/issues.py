"""Repair issue for an observed incompatible Paysmarti form."""

from urllib.parse import urlsplit

from homeassistant.helpers import issue_registry

from .const import DOMAIN
from .models import UnexpectedPage


def record_page_error(hass, entry, error: UnexpectedPage, http_status: int | None = None) -> None:
    """Retain only allowlisted structural metadata."""
    entry.runtime_data.last_error = {
        "stage": error.stage,
        "code": error.code,
        "http_status": http_status,
        "path": urlsplit(error.path).path,
        "field_names": list(error.field_names),
    }
    issue_registry.async_create_issue(
        hass, DOMAIN, f"form_changed_{entry.entry_id}",
        config_entry_id=entry.entry_id,
        is_fixable=False,
        is_persistent=True,
        severity=issue_registry.IssueSeverity.ERROR,
        translation_key="form_changed",
    )


def clear_page_error(hass, entry) -> None:
    """Remove a stale form issue after a compatible operation."""
    entry.runtime_data.last_error = None
    issue_registry.async_delete_issue(hass, DOMAIN, f"form_changed_{entry.entry_id}")
