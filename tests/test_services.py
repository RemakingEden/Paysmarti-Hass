"""Actions exercise the booking boundary without Paysmarti traffic."""

import asyncio
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.core import SupportsResponse

from custom_components.paysmarti_visitor.booking import PaysmartiBooker
from custom_components.paysmarti_visitor.models import AuthenticationFailed, BookingContext, BookingReceipt, BookingRejected, InteractiveChallenge, OutcomeUnknown, UnexpectedPage, UnexpectedRedirect, VehicleOption
from custom_components.paysmarti_visitor.services import book_visitor, get_last_result, list_saved_registrations


START = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fake_issue_deletion(monkeypatch):
    monkeypatch.setattr("custom_components.paysmarti_visitor.issues.issue_registry.async_delete_issue", lambda *a, **kw: None)


class Journal:
    def __init__(self):
        self.events = []
        self.current = None

    async def begin(self, mask):
        self.events.append(("begin", mask))
        self.current = SimpleNamespace(id="attempt", status="preparing", masked_registration=mask, safe_details={})
        return self.current

    async def mark_commit_started(self, attempt_id):
        self.events.append(("commit_started", attempt_id))
        self.current.status = "commit_started"

    async def finish(self, attempt_id, status, details):
        self.events.append(("finish", status, details))
        self.current.status = status
        self.current.safe_details = details

    async def last(self):
        return self.current


class Booker:
    def __init__(self, *, error=None):
        self.requests = []
        self.error = error
        self.wait = None

    async def prepare(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.wait:
            await self.wait.wait()
        return SimpleNamespace(registration=(request.new_registration or "AA00AAA").replace(" ", "").upper())

    async def commit(self, prepared):
        if self.error:
            raise self.error
        return BookingReceipt(prepared.registration, START, START + timedelta(days=1), "ref")


def runtime(booker=None, enabled=False):
    state = SimpleNamespace(booker=booker or Booker(), journal=Journal(), lock=asyncio.Lock(),
                           enable_save_new=enabled, session=object(), context=object(), last_error=None,
                           entry=SimpleNamespace(entry_id="one"),
                           authenticator=SimpleNamespace(ensure_visitor_session=_ensure_session))
    state.entry.runtime_data = state
    return state


async def _ensure_session():
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "new", "saved"])
async def test_actions_renew_session_before_page_access(monkeypatch, operation):
    state = runtime()
    events = []
    async def ensure():
        events.append("ensure")
    async def vehicles(session, context):
        events.append("vehicles")
        return [VehicleOption("v1", "AA00AAA", "AA00AAA")]
    async def prepare(request):
        events.append("prepare")
        return SimpleNamespace(registration="AA00AAA")
    state.authenticator.ensure_visitor_session = ensure
    state.booker.prepare = prepare
    monkeypatch.setattr("custom_components.paysmarti_visitor.services.list_saved_vehicles", vehicles)
    if operation == "list":
        await list_saved_registrations(None, state)
    else:
        await book_visitor(None, state, **({"saved_vehicle_id": "v1"} if operation == "saved" else {"new_registration": "AA00AAA"}))
    assert events[0] == "ensure"
    assert events[1] == ("vehicles" if operation != "new" else "prepare")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "book"])
@pytest.mark.parametrize("failure,key", [(AuthenticationFailed(), "authentication_failed"),
    (InteractiveChallenge(), "login_challenge"), (UnexpectedRedirect(), "unexpected_redirect")])
async def test_session_renewal_failure_is_safe_and_never_submits(failure, key, operation):
    state = runtime()
    async def ensure():
        raise failure
    state.authenticator.ensure_visitor_session = ensure
    with pytest.raises(HomeAssistantError) as error:
        if operation == "list":
            await list_saved_registrations(None, state)
        else:
            await book_visitor(None, state, new_registration="AA00AAA")
    assert error.value.translation_key == key
    assert state.booker.requests == []
    assert state.journal.events == []


@pytest.mark.asyncio
async def test_new_text_takes_precedence_and_booked_times_are_journaled():
    state = runtime()
    result = await book_visitor(None, state, saved_vehicle_id="old", new_registration=" bb 00 bbb ")
    assert state.booker.requests[0].new_registration == "BB00BBB"
    assert state.booker.requests[0].saved_vehicle_id is None
    assert result == {"registration": "BB00BBB", "starts_at": START.isoformat(),
                      "ends_at": (START + timedelta(days=1)).isoformat(), "reference": "ref"}
    assert state.journal.events[0] == ("begin", "****BBB")
    assert state.journal.events[1] == ("commit_started", "attempt")
    assert state.journal.events[2][1:] == ("booked", {"starts_at": START.isoformat(), "ends_at": (START + timedelta(days=1)).isoformat()})


@pytest.mark.asyncio
async def test_save_disabled_rejects_before_prepare():
    state = runtime()
    with pytest.raises(ServiceValidationError):
        await book_visitor(None, state, new_registration="BB00BBB", save=True)
    assert state.booker.requests == []


@pytest.mark.asyncio
async def test_invalid_registration_rejects_before_network_or_journal():
    state = runtime()
    with pytest.raises(ServiceValidationError):
        await book_visitor(None, state, new_registration="AB 123456789")
    assert state.booker.requests == []
    assert state.journal.events == []


@pytest.mark.asyncio
async def test_precommit_failure_is_not_booked():
    state = runtime(Booker(error=ValueError("bad registration")))
    with pytest.raises(ServiceValidationError):
        await book_visitor(None, state, new_registration="BB00BBB")
    assert state.journal.current.status == "not_booked"


@pytest.mark.asyncio
async def test_transport_failure_before_commit_finishes_not_booked():
    state = runtime(Booker(error=OSError("connection lost")))
    with pytest.raises(HomeAssistantError):
        await book_visitor(None, state, new_registration="BB00BBB")
    assert state.journal.current.status == "not_booked"


@pytest.mark.asyncio
async def test_journal_failure_prevents_final_submission():
    class FailingJournal(Journal):
        async def begin(self, mask):
            raise OSError("disk unavailable")
    state = runtime()
    state.journal = FailingJournal()
    with pytest.raises(HomeAssistantError):
        await book_visitor(None, state, new_registration="BB00BBB")
    assert state.booker.requests == []


@pytest.mark.asyncio
async def test_initial_notification_failure_is_translated_and_prevents_submission():
    class FailingJournal(Journal):
        async def begin(self, mask):
            raise RuntimeError("notifications unavailable")
    state = runtime()
    state.journal = FailingJournal()
    with pytest.raises(HomeAssistantError) as error:
        await book_visitor(None, state, new_registration="BB00BBB")
    assert error.value.translation_key == "journal_unavailable"
    assert state.booker.requests == []


@pytest.mark.asyncio
async def test_changed_field_name_still_finishes_precommit_attempt(monkeypatch):
    state = runtime(Booker(error=UnexpectedPage("StreetSelected", "/StepOne?secret=1", code="StreetSelected_changed")))
    monkeypatch.setattr("custom_components.paysmarti_visitor.issues.issue_registry.async_create_issue", lambda *a, **kw: None)
    with pytest.raises(ServiceValidationError):
        await book_visitor(object(), state, new_registration="BB00BBB")
    assert state.journal.current.status == "not_booked"
    assert state.journal.current.safe_details == {"stage": "preparation", "error_code": "form_changed"}
    assert state.last_error["path"] == "/StepOne"


@pytest.mark.asyncio
async def test_unknown_after_commit_is_preserved():
    class UnknownBooker(Booker):
        async def commit(self, prepared):
            raise OutcomeUnknown("ambiguous")
    state = runtime(UnknownBooker())
    with pytest.raises(HomeAssistantError):
        await book_visitor(None, state, new_registration="BB00BBB")
    assert state.journal.current.status == "outcome_unknown"


@pytest.mark.asyncio
async def test_commit_preflight_rejection_is_known_not_booked():
    class RejectingBooker(Booker):
        async def commit(self, prepared):
            raise BookingRejected("token missing before POST")
    state = runtime(RejectingBooker())
    with pytest.raises(ServiceValidationError) as error:
        await book_visitor(None, state, new_registration="BB00BBB")
    assert error.value.translation_key == "booking_rejected"
    assert state.journal.current.status == "not_booked"
    assert state.journal.current.safe_details["error_code"] == "booking_rejected"


@pytest.mark.asyncio
async def test_cancellation_after_commit_boundary_is_unknown():
    entered = asyncio.Event()
    class HangingBooker(Booker):
        async def commit(self, prepared):
            entered.set()
            await asyncio.Event().wait()
    state = runtime(HangingBooker())
    task = asyncio.create_task(book_visitor(None, state, new_registration="BB00BBB"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state.journal.current.status == "outcome_unknown"


@pytest.mark.asyncio
async def test_bookings_are_serialized():
    booker = Booker()
    booker.wait = asyncio.Event()
    state = runtime(booker)
    first = asyncio.create_task(book_visitor(None, state, new_registration="AA00AAA"))
    await asyncio.sleep(0)
    second = asyncio.create_task(book_visitor(None, state, new_registration="BB00BBB"))
    await asyncio.sleep(0)
    assert len(booker.requests) == 1
    booker.wait.set()
    await asyncio.gather(first, second)
    assert len(booker.requests) == 2


@pytest.mark.asyncio
async def test_saved_list_and_last_result(monkeypatch):
    state = runtime()
    async def vehicles(session, context):
        return [VehicleOption("v1", "AA00AAA", "AA00AAA (Family)")]
    monkeypatch.setattr("custom_components.paysmarti_visitor.services.list_saved_vehicles", vehicles)
    assert await list_saved_registrations(None, state) == {"vehicles": [{"id": "v1", "registration": "AA00AAA", "label": "AA00AAA (Family)"}], "can_save_new": False}
    await book_visitor(None, state, new_registration="BB00BBB")
    assert (await get_last_result(state))["status"] == "booked"


@pytest.mark.asyncio
async def test_confirmed_save_refreshes_saved_list(monkeypatch):
    state = runtime(enabled=True)
    calls = []
    async def vehicles(session, context):
        calls.append(1)
        return []
    monkeypatch.setattr("custom_components.paysmarti_visitor.services.list_saved_vehicles", vehicles)
    await book_visitor(None, state, new_registration="BB00BBB", save=True)
    assert calls == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError("slow refresh"), OSError("connection lost")])
async def test_confirmed_save_returns_receipt_and_safe_warning_if_refresh_fails(monkeypatch, failure):
    state = runtime(enabled=True)
    async def vehicles(session, context):
        raise failure
    monkeypatch.setattr("custom_components.paysmarti_visitor.services.list_saved_vehicles", vehicles)
    result = await book_visitor(None, state, new_registration="BB00BBB", save=True)
    assert result["reference"] == "ref"
    assert result["refresh_warning"] == "saved_list_refresh_failed"
    assert state.journal.current.status == "booked"


@pytest.mark.asyncio
async def test_changed_form_on_post_save_refresh_keeps_receipt_and_creates_issue(monkeypatch):
    state = runtime(enabled=True)
    created = []
    monkeypatch.setattr("custom_components.paysmarti_visitor.issues.issue_registry.async_create_issue", lambda *a, **kw: created.append((a, kw)))
    async def vehicles(session, context):
        raise UnexpectedPage("vehicle", "/StepOne?token=secret", ("VrmSelected",))
    monkeypatch.setattr("custom_components.paysmarti_visitor.services.list_saved_vehicles", vehicles)
    result = await book_visitor(object(), state, new_registration="BB00BBB", save=True)
    assert result["reference"] == "ref"
    assert result["refresh_warning"] == "saved_list_refresh_failed"
    assert state.journal.current.status == "booked"
    assert state.last_error["path"] == "/StepOne"
    assert len(created) == 1


@pytest.mark.asyncio
async def test_saved_booking_uses_fixture_transport_and_returns_verified_receipt(monkeypatch):
    class Response:
        def __init__(self, body, status=200, headers=None):
            self.body, self.status, self.headers = body, status, headers or {}
        async def text(self):
            return self.body
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False

    class Session:
        def __init__(self, responses):
            self.responses = deque(responses)
            self.calls = []
        def request(self, method, url, *, data=None, allow_redirects=True, timeout=None):
            self.calls.append((method, url))
            return self.responses.popleft()

    fixture = Path(__file__).parent / "fixtures"
    page = lambda name: Response((fixture / name).read_text())
    session = Session([
        page("permit.html"), page("step_one_saved.html"),
        page("permit.html"), page("step_one_saved.html"), page("step_two.html"),
        Response("", 302, {"Location": "/LeedsCC/BookParkingSuccess"}), page("success.html"),
    ])
    context = BookingContext("fixture-permit", "Fixture Visitor Permit", "fixture-street", "Fixture Street")
    monkeypatch.setattr("custom_components.paysmarti_visitor.booking._now", lambda: datetime(2026, 11, 13, 10, 10, tzinfo=ZoneInfo("Europe/London")))
    state = runtime(PaysmartiBooker(session, context))
    state.session, state.context = session, context
    result = await book_visitor(None, state, saved_vehicle_id="fixture-vehicle")
    assert result["registration"] == "AA00AAA"
    assert result["reference"] == "FIXTURE-BOOKING"
    assert state.journal.events[0] == ("begin", "****AAA")
    assert state.journal.events[1] == ("commit_started", "attempt")
    assert session.calls[-2:] == [
        ("POST", "https://visitor.paysmarti.co.uk/LeedsCC/BookParking/StepTwo"),
        ("GET", "https://visitor.paysmarti.co.uk/LeedsCC/BookParkingSuccess"),
    ]


@pytest.mark.asyncio
async def test_actions_are_registered_with_entry_target_and_response():
    from custom_components.paysmarti_visitor import async_setup

    registered = {}
    def register(domain, name, handler, *, schema, supports_response):
        assert domain == "paysmarti_visitor"
        registered[name] = (handler, schema, supports_response)

    state = runtime()
    async def register_paths(paths):
        pass
    hass = SimpleNamespace(
        http=SimpleNamespace(async_register_static_paths=register_paths),
        services=SimpleNamespace(async_register=register),
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: state.entry if entry_id == "one" else None),
    )
    assert await async_setup(hass, {})
    assert set(registered) == {"book_visitor", "list_saved_registrations", "get_last_result"}
    assert all(item[2] == SupportsResponse.ONLY for item in registered.values())
    call = SimpleNamespace(data=registered["get_last_result"][1]({"config_entry_id": "one"}))
    assert (await registered["get_last_result"][0](call))["status"] == "none"
    with pytest.raises(ServiceValidationError):
        await registered["get_last_result"][0](SimpleNamespace(data={"config_entry_id": "other"}))


@pytest.mark.asyncio
async def test_single_account_actions_accept_omitted_entry_id():
    from custom_components.paysmarti_visitor.services import async_register_services

    registered = {}
    def register(domain, name, handler, *, schema, supports_response):
        registered[name] = (handler, schema)
    state = runtime()
    hass = SimpleNamespace(
        services=SimpleNamespace(async_register=register),
        config_entries=SimpleNamespace(async_entries=lambda domain: [state.entry],
                                       async_get_entry=lambda entry_id: state.entry if entry_id == "one" else None),
    )
    await async_register_services(hass)
    handler, schema = registered["get_last_result"]
    assert (await handler(SimpleNamespace(data=schema({}))))["status"] == "none"


@pytest.mark.asyncio
async def test_omitted_entry_id_rejects_multiple_accounts():
    from custom_components.paysmarti_visitor.services import async_register_services

    registered = {}
    def register(domain, name, handler, *, schema, supports_response):
        registered[name] = (handler, schema)
    first, second = runtime(), runtime()
    hass = SimpleNamespace(
        services=SimpleNamespace(async_register=register),
        config_entries=SimpleNamespace(async_entries=lambda domain: [first.entry, second.entry],
                                       async_get_entry=lambda entry_id: None),
    )
    await async_register_services(hass)
    handler, schema = registered["get_last_result"]
    with pytest.raises(ServiceValidationError):
        await handler(SimpleNamespace(data=schema({})))
