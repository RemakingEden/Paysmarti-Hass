"""Durable, redacted booking outcomes and persistent notifications."""

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)
_FINAL = ("booked", "not_booked", "outcome_unknown")
_DETAIL_KEYS = frozenset({"stage", "error_code", "starts_at", "ends_at"})
_MASKED = re.compile(r"\*+[A-Z0-9]{0,3}\Z")
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
    notification_dismissed: bool = False

    @property
    def notification_id(self) -> str:
        return f"paysmarti_visitor_{self.id}"


def _validate_mask(mask: str) -> None:
    if not isinstance(mask, str) or len(mask) > 10 or not _MASKED.fullmatch(mask):
        raise ValueError("Registration must be masked before journaling")


def mask_registration(registration: str) -> str:
    """Hide at least one character of a normalized 1–10 character plate."""
    normalized = re.sub(r"\s+", "", registration).upper()
    if not re.fullmatch(r"[A-Z0-9]{1,10}", normalized):
        raise ValueError("Invalid registration")
    visible = min(3, len(normalized) - 1)
    return "*" * (len(normalized) - visible) + (normalized[-visible:] if visible else "")


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


def _validate_booked_times(details: dict[str, str]) -> None:
    if not {"starts_at", "ends_at"}.issubset(details):
        raise ValueError("Booked outcome requires verified start and end times")
    start = datetime.fromisoformat(details["starts_at"])
    end = datetime.fromisoformat(details["ends_at"])
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("Booked outcome requires ordered timezone-aware times")


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
        self._store = store if store is not None else Store(
            hass, 1, f"paysmarti_visitor.{entry_id}.last_attempt", atomic_writes=True
        )
        self._notify = notify if notify is not None else persistent_notification.async_create
        self._unsub_notification = None
        if hasattr(hass, "data") and hasattr(hass, "async_create_task"):
            self._unsub_notification = async_dispatcher_connect(
                hass, persistent_notification.SIGNAL_PERSISTENT_NOTIFICATIONS_UPDATED,
                self._on_notification_update,
            )

    def close(self) -> None:
        """Stop watching dismissals when the config entry unloads."""
        if self._unsub_notification is not None:
            self._unsub_notification()
            self._unsub_notification = None

    @callback
    def _on_notification_update(self, update_type, notifications) -> None:
        if update_type == persistent_notification.UpdateType.REMOVED:
            dismissed = {notification_id: notice.get("message")
                         for notification_id, notice in notifications.items()}
            self._hass.async_create_task(self._record_dismissal(dismissed))

    async def _record_dismissal(self, dismissed: dict[str, str | None]) -> None:
        current = await self.last()
        if (current is None or current.status not in _FINAL
                or current.notification_pending or current.notification_dismissed
                or dismissed.get(current.notification_id) != _message(current)):
            return
        try:
            await self._save(replace(current, notification_dismissed=True,
                                     notification_pending=False))
        except OSError:
            _LOGGER.warning("Could not record booking notification dismissal")

    async def last(self) -> Attempt | None:
        data = await self._store.async_load()
        return Attempt(**data) if data is not None else None

    async def _save(self, attempt: Attempt) -> None:
        expected = asdict(attempt)
        await self._store.async_save(expected)
        # Store.async_save can log and swallow WriteError, or defer a write while
        # Core is stopping. Store.async_load can then return pending memory data.
        # Only the physical file is evidence that the commit boundary survived.
        try:
            actual = await asyncio.to_thread(
                lambda: json.loads(Path(self._store.path).read_text(encoding="utf-8"))["data"]
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise OSError("Attempt state was not durably written") from exc
        if actual != expected:
            raise OSError("Attempt state was not durably written")

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
        if status == "booked":
            _validate_booked_times(safe_details)
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
        elif not current.notification_dismissed:
            try:
                if current.notification_pending:
                    await self._notify_and_clear(current)
                else:
                    self._update_notification(current)
            except Exception:
                _LOGGER.warning("Could not restore booking attempt notification")
        return await self.last()
