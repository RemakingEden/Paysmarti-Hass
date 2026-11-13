"""Home Assistant lifecycle for the Paysmarti visitor account."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import probatio as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.http import StaticPathConfig
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant import loader

from .auth import PaysmartiAuthenticator
from .booking import PaysmartiBooker
from .const import ALLOWED_HOSTS
from .discovery import discover_context
from .models import AuthenticationFailed, BookingContext, InteractiveChallenge, UnexpectedPage, UnexpectedRedirect
from .outcome import AttemptJournal
from .services import async_register_services
from .session import create_paysmarti_session


CONFIG_SCHEMA = vol.Schema({}, extra=vol.ALLOW_EXTRA)
CARD_URL = "/api/paysmarti_visitor/paysmarti-visitor-card.js"


async def _register_dashboard_resource(hass: HomeAssistant) -> None:
    """Make the card available in the picker when dashboards use UI resources."""
    lovelace = hass.data.get("lovelace") if hasattr(hass, "data") else None
    if lovelace is None or lovelace.resource_mode != "storage":
        return
    resources = lovelace.resources
    await resources.async_get_info()  # Ensure the collection is loaded before reading or writing.
    version = (await loader.async_get_integration(hass, "paysmarti_visitor")).version
    url = f"{CARD_URL}?v={version}"
    for item in resources.async_items():
        if item.get("url", "").split("?", 1)[0] == CARD_URL:
            if item["url"] != url or item.get("res_type") != "module":
                await resources.async_update_item(item["id"], {"url": url, "res_type": "module"})
            return
    await resources.async_create_item({"url": url, "res_type": "module"})


@dataclass
class PaysmartiRuntime:
    """One account's isolated session and serialized booking state."""

    authenticator: PaysmartiAuthenticator
    context: BookingContext
    booker: PaysmartiBooker
    lock: asyncio.Lock
    session: object
    entry: ConfigEntry
    journal: AttemptJournal | None = None
    last_error: dict | None = None

    @property
    def enable_save_new(self) -> bool:
        return self.entry.options.get("enable_save_new", False) is True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Login and revalidate the account's chosen permit and street."""
    journal = AttemptJournal(hass, entry.entry_id) if hasattr(hass, "config") else None
    if journal is not None:
        await journal.recover_on_startup()
    session = create_paysmarti_session(hass)
    authenticator = PaysmartiAuthenticator(session, ALLOWED_HOSTS)
    try:
        await authenticator.login(entry.data["email"], entry.data["password"])
        context = await discover_context(session, entry.data["permit_label"], entry.data["street_label"])
    except (AuthenticationFailed, InteractiveChallenge) as exc:
        session.detach()
        if journal is not None:
            journal.close()
        raise ConfigEntryAuthFailed("Paysmarti authentication needs attention") from exc
    except (UnexpectedPage, UnexpectedRedirect, aiohttp.ClientError, TimeoutError, OSError) as exc:
        session.detach()
        if journal is not None:
            journal.close()
        raise ConfigEntryNotReady("Paysmarti account or booking context unavailable") from exc
    entry.runtime_data = PaysmartiRuntime(
        authenticator, context, PaysmartiBooker(session, context), asyncio.Lock(), session, entry,
        journal=journal,
    )
    return True


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Install entry-targeted actions once for the integration domain."""
    await async_register_services(hass)
    await hass.http.async_register_static_paths([StaticPathConfig(
        CARD_URL,
        str(Path(__file__).parent / "frontend" / "paysmarti-visitor-card.js"),
        cache_headers=False,
    )])
    await _register_dashboard_resource(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Discard the account's cookies on unload."""
    if entry.runtime_data.journal is not None:
        entry.runtime_data.journal.close()
    entry.runtime_data.session.detach()
    entry.runtime_data = None
    return True
