"""Durable, redacted booking outcomes and persistent notifications."""

import logging
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from homeassistant.components import persistent_notification
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)
_FINAL = ("booked", "not_booked", "outcome_unknown")
_DETAIL_KEYS = frozenset({"stage", "error_code", "starts_at", "ends_at"})
_MASKED = re.compile(r"[A-Z0-9*]{2,12}\Z")
_STAGES = frozenset({
    "authentication", "permit", "street", "vehicle", "booking", "summary", "prompt",
    "prepare", "preparation", "commit", "success", "notification",
})
_ERROR_CODES = frozenset({
    "authentication_failed", "login_challenge", "unexpected_redirect", "form_changed",
    "token_missing", "vehicle_missing", "street_changed", "permit_changed",
    "summary_mismatch", "success_unverifiable", "parking_changed", "prompt_changed",
    "registration_changed", "session_changed", "summary_changed", "vehicle_changed",
    "vehicle_unverifiable", "booking_rejected", "outcome_unknown",
    "interrupted_before_commit", "interrupted_after_commit",
})


@dataclass(frozen=True)
class Attempt:
    id: str
    started_at: str
    masked_registration: str
    status: Literal["preparing", "commit_started", "booked", "not_booked", "outcome_unknown"]
    safe_details: dict[str, str]
    notification_pending: bool = False

    @property
    def notification_id(self) -> str:
        return f"paysmarti_visitor_{self.id}"


def _validate_mask(mask: str) -> None:
    if not _MASKED.fullmatch(mask) or "*" not in mask or sum(char != "*" for char in mask) > 4:
        raise ValueError("Registration must be masked before journaling")


def _validate_details(details: dict[str, str]) -> None:
    if not isinstance(details, dict) or set(details) - _DETAIL_KEYS:
        raise ValueError("Unsafe attempt detail key")
    for key, value in details.items():
        if not isinstance(value, str):
            raise ValueError("Attempt details must be strings")
        if (key == "stage" and value not in _STAGES) or (key == "error_code" and value not in _ERROR_CODES):
            raise ValueError("Attempt stage or code is not a safe category")
        if key in {"starts_at", "ends_at"}:
            try:
                datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Attempt time must be ISO formatted") from exc


def _message(attempt: Attempt) -> str:
    registration = attempt.masked_registration
    details = attempt.safe_details
    if attempt.status == "preparing":
        return f"Booking in progress for {registration}."
    if attempt.status == "commit_started":
        return f"Booking submission in progress for {registration}."
    if attempt.status == "booked":
        times = ""
        if details.get("starts_at") and details.get("ends_at"):
            times = f" From {details['starts_at']} to {details['ends_at']}."
        return f"Booked for {registration}.{times}"
    if attempt.status == "not_booked":
        stage = details.get("stage", "preparation")
        return (f"Not booked: final submission was not sent for {registration}. "
                f"Stage: {stage}. Check the details and try again.")
    return (f"Outcome unknown for {registration}. The final submission may have succeeded. "
            "Check Paysmarti before another attempt.")


class AttemptJournal:
    """Keep only the latest attempt in HA storage; keep each notification visible."""

    def __init__(self, hass, entry_id: str, *, store=None, notify=None) -> None:
        self._hass = hass
        self._store = store if store is not None else Store(hass, 1, f"paysmarti_visitor.{entry_id}.last_attempt")
        self._notify = notify if notify is not None else persistent_notification.async_create

    async def last(self) -> Attempt | None:
        data = await self._store.async_load()
        return Attempt(**data) if data is not None else None

    async def _save(self, attempt: Attempt) -> None:
        await self._store.async_save(asdict(attempt))

    def _update_notification(self, attempt: Attempt) -> None:
        self._notify(self._hass, _message(attempt), title="Paysmarti visitor booking",
                     notification_id=attempt.notification_id)

    async def _notify_and_clear(self, attempt: Attempt) -> None:
        self._update_notification(attempt)
        await self._save(replace(attempt, notification_pending=False))

    async def begin(self, masked_registration: str) -> Attempt:
        """Return only after the initial record and notification both exist."""
        _validate_mask(masked_registration)
        attempt = Attempt(uuid4().hex, datetime.now(timezone.utc).isoformat(), masked_registration,
                          "preparing", {}, True)
        await self._save(attempt)
        await self._notify_and_clear(attempt)
        return replace(attempt, notification_pending=False)

    async def mark_commit_started(self, attempt_id: str) -> None:
        """Persist the commit boundary before the caller may send its final POST."""
        current = await self.last()
        if current is None or current.id != attempt_id or current.status != "preparing":
            raise ValueError("Attempt is not preparing")
        updated = Attempt(current.id, current.started_at, current.masked_registration,
                          "commit_started", current.safe_details, True)
        await self._save(updated)
        try:
            await self._notify_and_clear(updated)
        except Exception:
            _LOGGER.warning("Could not update booking attempt notification after commit boundary")

    async def finish(self, attempt_id: str,
                     status: Literal["booked", "not_booked", "outcome_unknown"],
                     safe_details: dict[str, str]) -> None:
        """Persist the result first; notification failure can be repaired on startup."""
        if status not in _FINAL:
            raise ValueError("Invalid final status")
        _validate_details(safe_details)
        current = await self.last()
        if current is None or current.id != attempt_id:
            raise ValueError("Attempt not found")
        if ((status == "not_booked" and current.status != "preparing")
                or (status != "not_booked" and current.status != "commit_started")):
            raise ValueError("Invalid attempt transition")
        updated = Attempt(current.id, current.started_at, current.masked_registration,
                          status, dict(safe_details), True)
        await self._save(updated)
        try:
            await self._notify_and_clear(updated)
        except Exception:
            _LOGGER.warning("Could not update final booking attempt notification")

    async def recover_on_startup(self) -> Attempt | None:
        """Classify interrupted attempts without issuing a booking request."""
        current = await self.last()
        if current is None:
            return None
        if current.status == "preparing":
            await self.finish(current.id, "not_booked",
                              {"stage": "preparation", "error_code": "interrupted_before_commit"})
        elif current.status == "commit_started":
            await self.finish(current.id, "outcome_unknown",
                              {"stage": "commit", "error_code": "interrupted_after_commit"})
        elif current.notification_pending:
            try:
                await self._notify_and_clear(current)
            except Exception:
                _LOGGER.warning("Could not restore booking attempt notification")
        return await self.last()
