"""Durable attempt and notification contracts, without external requests."""

import asyncio
import copy
import json
import tempfile
from pathlib import Path

import pytest
from homeassistant.components import persistent_notification
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.paysmarti_visitor.outcome import AttemptJournal, mask_registration


class MemoryStore:
    def __init__(self, events=None):
        self.data = None
        self.fail_save = False
        self.silent_fail = False
        self.events = events if events is not None else []
        self._directory = tempfile.TemporaryDirectory()
        self.path = Path(self._directory.name) / "attempt.json"

    async def async_load(self):
        return copy.deepcopy(self.data)

    async def async_save(self, data):
        self.events.append("save")
        if self.fail_save:
            raise OSError("storage unavailable")
        self.data = copy.deepcopy(data)
        if not self.silent_fail:
            self.path.write_text(json.dumps({"version": 1, "key": "attempt", "data": data}))


class Notifications:
    def __init__(self, events=None):
        self.items = {}
        self.fail = False
        self.events = events if events is not None else []

    def __call__(self, hass, message, title=None, notification_id=None):
        self.events.append("notify")
        if self.fail:
            raise RuntimeError("notifications unavailable")
        self.items[notification_id] = (title, message)


def journal(store=None, notifications=None):
    return AttemptJournal(object(), "entry-1", store=store or MemoryStore(), notify=notifications or Notifications())


def run(coro):
    return asyncio.run(coro)


def test_begin_persists_before_creating_in_progress_notification():
    events = []
    store, notices = MemoryStore(events), Notifications(events)
    attempt = run(journal(store, notices).begin("***AAA"))
    assert store.data["status"] == "preparing"
    assert events == ["save", "notify", "save"]
    assert list(notices.items) == [f"paysmarti_visitor_{attempt.id}"]
    assert "Booking in progress" in next(iter(notices.items.values()))[1]
    assert "***AAA" in next(iter(notices.items.values()))[1]


@pytest.mark.parametrize("status,phrase", [
    ("booked", "Booked"),
    ("not_booked", "Not booked: final submission was not sent"),
    ("outcome_unknown", "Outcome unknown"),
])
def test_finish_updates_same_notification_and_reloads_last_result(status, phrase):
    store, notices = MemoryStore(), Notifications()
    first = journal(store, notices)
    attempt = run(first.begin("***AAA"))
    if status != "not_booked":
        run(first.mark_commit_started(attempt.id))
    details = {"stage": "booking", "error_code": "form_changed"}
    if status == "booked":
        details.update({"starts_at": "2026-11-13T10:05:00+00:00", "ends_at": "2026-11-13T23:59:00+00:00"})
    run(first.finish(attempt.id, status, details))
    restored = run(journal(store, notices).last())
    assert restored.status == status
    assert restored.masked_registration == "***AAA"
    assert restored.safe_details == details
    assert len(notices.items) == 1
    assert phrase in next(iter(notices.items.values()))[1]


def test_known_preflight_rejection_after_commit_boundary_is_not_booked():
    store = MemoryStore()
    notices = Notifications()
    attempts = journal(store, notices)
    attempt = run(attempts.begin("***AAA"))
    run(attempts.mark_commit_started(attempt.id))
    run(attempts.finish(attempt.id, "not_booked", {"stage": "commit", "error_code": "booking_rejected"}))
    assert store.data["status"] == "not_booked"
    assert "final submission was not sent" in next(iter(notices.items.values()))[1]


def test_begin_save_or_notification_failure_never_returns_commit_authority():
    store, notices = MemoryStore(), Notifications()
    store.fail_save = True
    with pytest.raises(OSError):
        run(journal(store, notices).begin("***AAA"))
    assert notices.items == {}

    store.fail_save = False
    notices.fail = True
    with pytest.raises(RuntimeError):
        run(journal(store, notices).begin("***AAA"))
    assert store.data["status"] == "preparing"


def test_commit_start_save_failure_does_not_return_commit_authority():
    store = MemoryStore()
    attempts = journal(store)
    attempt = run(attempts.begin("***AAA"))
    store.fail_save = True
    with pytest.raises(OSError):
        run(attempts.mark_commit_started(attempt.id))
    assert store.data["status"] == "preparing"


def test_silent_store_write_failure_blocks_begin_and_commit():
    store = MemoryStore()
    store.silent_fail = True
    with pytest.raises(OSError):
        run(journal(store).begin("***AAA"))
    store.silent_fail = False
    attempts = journal(store)
    attempt = run(attempts.begin("***AAA"))
    store.silent_fail = True
    with pytest.raises(OSError):
        run(attempts.mark_commit_started(attempt.id))
    assert json.loads(store.path.read_text())["data"]["status"] == "preparing"


def test_startup_recovery_distinguishes_pre_commit_and_uncertain_submission():
    for before, after in (("preparing", "not_booked"), ("commit_started", "outcome_unknown")):
        store, notices = MemoryStore(), Notifications()
        attempts = journal(store, notices)
        attempt = run(attempts.begin("***AAA"))
        if before == "commit_started":
            run(attempts.mark_commit_started(attempt.id))
        restored = run(journal(store, notices).recover_on_startup())
        assert restored.status == after
        assert run(journal(store, notices).last()).status == after
        assert "interrupted" in restored.safe_details["error_code"]


def test_sensitive_values_are_rejected_before_storage_or_notification():
    store, notices = MemoryStore(), Notifications()
    attempts = journal(store, notices)
    with pytest.raises(ValueError):
        run(attempts.begin("AA00AAA"))
    assert store.data is None
    attempt = run(attempts.begin("***AAA"))
    with pytest.raises(ValueError):
        run(attempts.finish(attempt.id, "not_booked", {"password": "secret-value"}))
    with pytest.raises(ValueError):
        run(attempts.finish(attempt.id, "not_booked", {"error_code": "AA00AAA"}))
    with pytest.raises(ValueError):
        run(attempts.finish(attempt.id, "not_booked", {"error_code": "aa00aaa"}))
    assert "AA00AAA" not in repr(store.data) + repr(notices.items)
    assert "secret-value" not in repr(store.data) + repr(notices.items)


def test_final_notification_failure_preserves_stored_outcome_for_retry():
    store, notices = MemoryStore(), Notifications()
    attempts = journal(store, notices)
    attempt = run(attempts.begin("***AAA"))
    notices.fail = True
    run(attempts.finish(attempt.id, "not_booked", {"stage": "prepare", "error_code": "form_changed"}))
    assert store.data["status"] == "not_booked"
    assert store.data["notification_pending"] is True
    notices.fail = False
    run(journal(store, notices).recover_on_startup())
    assert store.data["notification_pending"] is False
    assert "Not booked" in next(iter(notices.items.values()))[1]


def test_completed_notification_is_restored_after_restart():
    store, notices = MemoryStore(), Notifications()
    attempts = journal(store, notices)
    attempt = run(attempts.begin("***AAA"))
    run(attempts.finish(attempt.id, "not_booked", {"stage": "prepare"}))
    notices.items.clear()
    run(journal(store, notices).recover_on_startup())
    assert "Not booked" in next(iter(notices.items.values()))[1]


def test_dismissed_completed_notification_stays_dismissed_after_restart():
    class FakeHass:
        def __init__(self):
            self.data = {}
            self.tasks = []

        def verify_event_loop_thread(self, *_):
            pass

        def async_create_task(self, coro, **_):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task

    async def scenario():
        hass, store, notices = FakeHass(), MemoryStore(), Notifications()
        attempts = AttemptJournal(hass, "entry-1", store=store, notify=notices)
        attempt = await attempts.begin("***AAA")
        await attempts.finish(attempt.id, "not_booked", {"stage": "prepare"})
        dismissed_message = notices.items[attempt.notification_id][1]
        notices.items.clear()
        async_dispatcher_send(hass, persistent_notification.SIGNAL_PERSISTENT_NOTIFICATIONS_UPDATED,
                              persistent_notification.UpdateType.REMOVED,
                              {attempt.notification_id: {"notification_id": attempt.notification_id,
                                                         "message": dismissed_message}})
        await asyncio.gather(*hass.tasks)
        assert store.data["notification_dismissed"] is True
        await AttemptJournal(hass, "entry-1", store=store, notify=notices).recover_on_startup()
        assert notices.items == {}
        attempts.close()

    run(scenario())


def test_old_in_progress_dismissal_cannot_hide_pending_final_outcome():
    class FakeHass:
        def __init__(self):
            self.data = {}
            self.tasks = []

        def verify_event_loop_thread(self, *_):
            pass

        def async_create_task(self, coro, **_):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task

    class InterleavingStore(MemoryStore):
        def __init__(self):
            super().__init__()
            self.on_final_save = None

        async def async_save(self, data):
            await super().async_save(data)
            if data["status"] == "not_booked" and data["notification_pending"] and self.on_final_save:
                self.on_final_save()

    async def scenario():
        hass, store, notices = FakeHass(), InterleavingStore(), Notifications()
        attempts = AttemptJournal(hass, "entry-1", store=store, notify=notices)
        attempt = await attempts.begin("***AAA")
        in_progress_message = notices.items[attempt.notification_id][1]

        def dismiss_old_notification():
            notices.items.clear()
            notices.fail = True
            async_dispatcher_send(hass, persistent_notification.SIGNAL_PERSISTENT_NOTIFICATIONS_UPDATED,
                                  persistent_notification.UpdateType.REMOVED,
                                  {attempt.notification_id: {"notification_id": attempt.notification_id,
                                                             "message": in_progress_message}})

        store.on_final_save = dismiss_old_notification
        await attempts.finish(attempt.id, "not_booked", {"stage": "prepare"})
        await asyncio.gather(*hass.tasks)
        assert store.data["notification_pending"] is True
        assert store.data["notification_dismissed"] is False
        notices.fail = False
        store.on_final_save = None
        await AttemptJournal(hass, "entry-1", store=store, notify=notices).recover_on_startup()
        assert "Not booked" in next(iter(notices.items.values()))[1]
        attempts.close()

    run(scenario())


def test_final_notice_dismissal_during_pending_clear_survives_restart():
    class FakeHass:
        def __init__(self):
            self.data = {}
            self.tasks = []

        def verify_event_loop_thread(self, *_):
            pass

        def async_create_task(self, coro, **_):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task

    class PausedStore(MemoryStore):
        def __init__(self):
            super().__init__()
            self.pause_final_clear = False
            self.clear_entered = asyncio.Event()
            self.release_clear = asyncio.Event()

        async def async_save(self, data):
            if (self.pause_final_clear and data["status"] == "not_booked"
                    and not data["notification_pending"] and not data["notification_dismissed"]):
                self.clear_entered.set()
                await self.release_clear.wait()
            await super().async_save(data)

    async def scenario():
        hass, store, notices = FakeHass(), PausedStore(), Notifications()
        attempts = AttemptJournal(hass, "entry-1", store=store, notify=notices)
        attempt = await attempts.begin("***AAA")
        store.pause_final_clear = True
        finishing = asyncio.create_task(attempts.finish(attempt.id, "not_booked", {"stage": "prepare"}))
        await store.clear_entered.wait()
        final_message = notices.items.pop(attempt.notification_id)[1]
        assert "Not booked" in final_message
        async_dispatcher_send(hass, persistent_notification.SIGNAL_PERSISTENT_NOTIFICATIONS_UPDATED,
                              persistent_notification.UpdateType.REMOVED,
                              {attempt.notification_id: {"notification_id": attempt.notification_id,
                                                         "message": final_message}})
        await asyncio.sleep(0)
        store.release_clear.set()
        await finishing
        await asyncio.gather(*hass.tasks)
        assert store.data["notification_dismissed"] is True
        store.pause_final_clear = False
        await AttemptJournal(hass, "entry-1", store=store, notify=notices).recover_on_startup()
        assert notices.items == {}
        attempts.close()

    run(scenario())


def test_booked_requires_verified_times_and_shows_them():
    store, notices = MemoryStore(), Notifications()
    attempts = journal(store, notices)
    attempt = run(attempts.begin("***AAA"))
    run(attempts.mark_commit_started(attempt.id))
    with pytest.raises(ValueError):
        run(attempts.finish(attempt.id, "booked", {}))
    run(attempts.finish(attempt.id, "booked", {
        "starts_at": "2026-11-13T10:05:00+00:00",
        "ends_at": "2026-11-13T23:59:00+00:00",
    }))
    message = next(iter(notices.items.values()))[1]
    assert "10:05" in message and "23:59" in message


@pytest.mark.parametrize("registration", ["A", "AB", "AB12", "AB12C", "AB12CDE", "ABCDEFGHIJ"])
def test_mask_registration_hides_at_least_one_real_character(registration):
    masked = mask_registration(registration)
    assert masked != registration
    assert len(masked) == len(registration)
    assert "*" in masked
    assert sum(a != b for a, b in zip(masked, registration)) >= 1


def test_card_dismissal_survives_restart_and_new_attempt_is_visible():
    store = MemoryStore()
    first = journal(store)
    attempt = run(first.begin("***AAA"))
    run(first.finish(attempt.id, "not_booked", {"stage": "prepare"}))
    assert run(first.dismiss(attempt.id)) is True
    assert run(journal(store).last()).card_dismissed is True
    second = run(journal(store).begin("***BBB"))
    assert second.card_dismissed is False
    assert run(journal(store).dismiss(attempt.id)) is False
    assert run(journal(store).last()).id == second.id


def test_normal_journal_does_not_create_system_wide_notification(monkeypatch):
    notices = []
    monkeypatch.setattr(persistent_notification, "async_create", lambda *args, **kwargs: notices.append(1))
    store = MemoryStore()
    hass = type("FakeHass", (), {"data": {}})()
    attempts = AttemptJournal(hass, "entry-1", store=store)
    attempt = run(attempts.begin("***AAA"))
    run(attempts.finish(attempt.id, "not_booked", {"stage": "prepare"}))
    run(attempts.recover_on_startup())
    assert notices == []
