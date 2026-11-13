"""Durable attempt and notification contracts, without external requests."""

import asyncio
import copy

import pytest

from custom_components.paysmarti_visitor.outcome import AttemptJournal


class MemoryStore:
    def __init__(self, events=None):
        self.data = None
        self.fail_save = False
        self.events = events if events is not None else []

    async def async_load(self):
        return copy.deepcopy(self.data)

    async def async_save(self, data):
        self.events.append("save")
        if self.fail_save:
            raise OSError("storage unavailable")
        self.data = copy.deepcopy(data)


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
    run(first.finish(attempt.id, status, {"stage": "booking", "error_code": "form_changed"}))
    restored = run(journal(store, notices).last())
    assert restored.status == status
    assert restored.masked_registration == "***AAA"
    assert restored.safe_details == {"stage": "booking", "error_code": "form_changed"}
    assert len(notices.items) == 1
    assert phrase in next(iter(notices.items.values()))[1]


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


def test_dismissed_final_notification_is_not_recreated_on_restart():
    store, notices = MemoryStore(), Notifications()
    attempts = journal(store, notices)
    attempt = run(attempts.begin("***AAA"))
    run(attempts.finish(attempt.id, "not_booked", {"stage": "prepare"}))
    notices.items.clear()
    run(journal(store, notices).recover_on_startup())
    assert notices.items == {}
