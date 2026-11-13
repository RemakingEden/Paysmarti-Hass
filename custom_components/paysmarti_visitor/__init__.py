"""Home Assistant lifecycle for the Paysmarti visitor account."""

import asyncio
from dataclasses import dataclass

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .auth import PaysmartiAuthenticator
from .booking import PaysmartiBooker
from .const import ALLOWED_HOSTS
from .discovery import discover_context
from .models import AuthenticationFailed, BookingContext, InteractiveChallenge, UnexpectedPage, UnexpectedRedirect
from .outcome import AttemptJournal
from .services import async_register_services


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
    session = async_create_clientsession(hass)
    authenticator = PaysmartiAuthenticator(session, ALLOWED_HOSTS)
    try:
        await authenticator.login(entry.data["email"], entry.data["password"])
        context = await discover_context(session, entry.data["permit_label"], entry.data["street_label"])
    except (AuthenticationFailed, InteractiveChallenge) as exc:
        session.detach()
        raise ConfigEntryAuthFailed("Paysmarti authentication needs attention") from exc
    except (UnexpectedPage, UnexpectedRedirect, aiohttp.ClientError, TimeoutError, OSError) as exc:
        session.detach()
        raise ConfigEntryNotReady("Paysmarti account or booking context unavailable") from exc
    entry.runtime_data = PaysmartiRuntime(
        authenticator, context, PaysmartiBooker(session, context), asyncio.Lock(), session, entry
    )
    if hasattr(hass, "config"):
        entry.runtime_data.journal = AttemptJournal(hass, entry.entry_id)
        await entry.runtime_data.journal.recover_on_startup()
    return True


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Install entry-targeted actions once for the integration domain."""
    await async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Discard the account's cookies on unload."""
    if entry.runtime_data.journal is not None:
        entry.runtime_data.journal.close()
    entry.runtime_data.session.detach()
    entry.runtime_data = None
    return True
