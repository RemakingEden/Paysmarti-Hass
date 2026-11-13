"""Home Assistant actions for safe visitor booking."""

import asyncio
import re
from dataclasses import asdict

import aiohttp
import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN
from .discovery import list_saved_vehicles
from .issues import clear_page_error, record_page_error
from .models import BookingInput, BookingRejected, OutcomeUnknown, UnexpectedPage
from .outcome import mask_registration


def _validation(key: str) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


def _error(key: str) -> HomeAssistantError:
    return HomeAssistantError(translation_domain=DOMAIN, translation_key=key)


async def list_saved_registrations(hass, state) -> dict:
    """Refresh choices from the account's currently visible forms."""
    async with state.lock:
        try:
            vehicles = await list_saved_vehicles(state.session, state.context)
        except UnexpectedPage as exc:
            record_page_error(hass, state.entry, exc)
            raise _error("form_changed") from exc
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise _error("cannot_connect") from exc
        clear_page_error(hass, state.entry)
        return {"vehicles": [asdict(vehicle) for vehicle in vehicles], "can_save_new": state.enable_save_new}


async def book_visitor(hass, state, *, saved_vehicle_id: str | None = None,
                       new_registration: str | None = None, save: bool = False,
                       nickname: str | None = None) -> dict:
    """Journal each submission boundary and return only a confirmed receipt."""
    if save and not state.enable_save_new:
        raise _validation("save_disabled")
    normalized = re.sub(r"\s+", "", new_registration or "").upper()
    if normalized and re.fullmatch(r"[A-Z0-9]{1,10}", normalized) is None:
        raise _validation("invalid_registration")
    if not normalized and not saved_vehicle_id:
        raise _validation("registration_required")
    if save and not normalized:
        raise _validation("save_requires_new")
    async with state.lock:
        if normalized:
            registration = normalized
        else:
            try:
                choices = await list_saved_vehicles(state.session, state.context)
            except UnexpectedPage as exc:
                record_page_error(hass, state.entry, exc)
                raise _error("form_changed") from exc
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                raise _error("cannot_connect") from exc
            matches = [vehicle for vehicle in choices if vehicle.id == saved_vehicle_id]
            if len(matches) != 1:
                raise _validation("vehicle_missing")
            registration = matches[0].registration
        try:
            attempt = await state.journal.begin(mask_registration(registration))
        except Exception as exc:
            raise _error("journal_unavailable") from exc
        committed = False
        try:
            prepared = await state.booker.prepare(BookingInput(
                None if normalized else saved_vehicle_id,
                normalized or None, save, nickname,
            ))
            await state.journal.mark_commit_started(attempt.id)
            committed = True
            receipt = await state.booker.commit(prepared)
            await state.journal.finish(attempt.id, "booked", {
                "starts_at": receipt.starts_at.isoformat(), "ends_at": receipt.ends_at.isoformat(),
            })
        except asyncio.CancelledError:
            await asyncio.shield(state.journal.finish(
                attempt.id, "outcome_unknown" if committed else "not_booked",
                {"stage": "commit" if committed else "preparation",
                 "error_code": "interrupted_after_commit" if committed else "interrupted_before_commit"},
            ))
            raise
        except OutcomeUnknown as exc:
            await state.journal.finish(attempt.id, "outcome_unknown", {"stage": "commit", "error_code": "outcome_unknown"})
            raise _error("outcome_unknown") from exc
        except BookingRejected as exc:
            await state.journal.finish(attempt.id, "not_booked", {
                "stage": "commit" if committed else "preparation", "error_code": "booking_rejected",
            })
            raise _validation("booking_rejected") from exc
        except (UnexpectedPage, ValueError) as exc:
            if isinstance(exc, UnexpectedPage):
                record_page_error(hass, state.entry, exc)
            await state.journal.finish(attempt.id, "outcome_unknown" if committed else "not_booked", {
                "stage": "commit" if committed else "preparation",
                "error_code": "outcome_unknown" if committed else ("form_changed" if isinstance(exc, UnexpectedPage) else "booking_rejected"),
            })
            raise (_error("outcome_unknown") if committed else _validation(
                "form_changed" if isinstance(exc, UnexpectedPage) else "booking_rejected")) from exc
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            await state.journal.finish(attempt.id, "outcome_unknown" if committed else "not_booked", {
                "stage": "commit" if committed else "preparation",
                "error_code": "outcome_unknown" if committed else "booking_rejected",
            })
            raise _error("outcome_unknown" if committed else "cannot_connect") from exc
        clear_page_error(hass, state.entry)
        refresh_warning = None
        if save:
            try:
                await list_saved_vehicles(state.session, state.context)
            except UnexpectedPage as exc:
                record_page_error(hass, state.entry, exc)
                refresh_warning = "saved_list_refresh_failed"
            except (aiohttp.ClientError, TimeoutError, OSError):
                refresh_warning = "saved_list_refresh_failed"
        result = {"registration": receipt.registration, "starts_at": receipt.starts_at.isoformat(),
                  "ends_at": receipt.ends_at.isoformat(), "reference": receipt.reference}
        if refresh_warning:
            result["refresh_warning"] = refresh_warning
        return result


async def get_last_result(state) -> dict:
    """Return the journal's redacted latest status."""
    attempt = await state.journal.last()
    if attempt is None:
        return {"status": "none"}
    return {"status": attempt.status, "started_at": getattr(attempt, "started_at", None),
            "masked_registration": attempt.masked_registration, "safe_details": attempt.safe_details}


async def async_register_services(hass) -> None:
    """Register entry-targeted actions with response support."""
    def state_for(call):
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or getattr(entry, "runtime_data", None) is None:
            raise _validation("entry_unavailable")
        return entry.runtime_data

    async def list_action(call):
        return await list_saved_registrations(hass, state_for(call))

    async def book_action(call):
        data = call.data
        return await book_visitor(hass, state_for(call), saved_vehicle_id=data.get("saved_vehicle_id"),
                                  new_registration=data.get("new_registration"), save=data.get("save", False),
                                  nickname=data.get("nickname"))

    async def result_action(call):
        return await get_last_result(state_for(call))

    entry = {vol.Required("config_entry_id"): str}
    hass.services.async_register(DOMAIN, "list_saved_registrations", list_action, schema=vol.Schema(entry), supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "book_visitor", book_action, schema=vol.Schema({
        **entry, vol.Optional("saved_vehicle_id"): str, vol.Optional("new_registration"): str,
        vol.Optional("save", default=False): bool, vol.Optional("nickname"): str,
    }), supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "get_last_result", result_action, schema=vol.Schema(entry), supports_response=SupportsResponse.ONLY)
