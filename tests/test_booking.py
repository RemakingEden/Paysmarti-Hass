"""Pre-commit booking tests against sanitized form responses."""

import asyncio
from collections import deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from custom_components.paysmarti_visitor.booking import PaysmartiBooker
from custom_components.paysmarti_visitor.models import (
    BookingContext, BookingInput, BookingRejected, OutcomeUnknown, UnexpectedPage,
)


FIXTURES = Path(__file__).parent / "fixtures"
PERMIT = (FIXTURES / "permit.html").read_text()
STEP = (FIXTURES / "step_one_saved.html").read_text()
PROMPT = (FIXTURES / "step_one_new_prompt.html").read_text()
SUMMARY = (FIXTURES / "step_two.html").read_text()
CONTEXT = BookingContext("fixture-permit", "Fixture Visitor Permit", "fixture-street", "Fixture Street")
NOW = datetime(2026, 11, 13, 10, 10, tzinfo=ZoneInfo("Europe/London"))


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}

    async def text(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class Session:
    def __init__(self, *bodies):
        self.responses = deque(body if isinstance(body, (Response, Exception)) else Response(body) for body in bodies)
        self.calls = []

    def request(self, method, url, *, data=None, allow_redirects=True, timeout=None):
        self.calls.append((method, url, data, allow_redirects, timeout))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def prepare(session, request):
    return asyncio.run(PaysmartiBooker(session, CONTEXT).prepare(request))


def new_summary(registration="BB00BBB", save=False, nickname=""):
    return (SUMMARY.replace("AA00AAA", registration)
            .replace('name="VrmSelected" value="fixture-vehicle"', 'name="VrmSelected" value=""')
            .replace('name="UseNewVrm" value="False"', 'name="UseNewVrm" value="True"')
            .replace('name="Vrm" value=""', f'name="Vrm" value="{registration}"')
            .replace('name="SaveThisVrm" value="False"', f'name="SaveThisVrm" value="{str(save)}"')
            .replace('name="VehicleKnownAs" value=""', f'name="VehicleKnownAs" value="{nickname}"'))


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("custom_components.paysmarti_visitor.booking._now", lambda: NOW)


def test_prepare_saved_vehicle_uses_current_selection_and_stops_at_step_two():
    session = Session(PERMIT, STEP, SUMMARY)
    result = prepare(session, BookingInput("fixture-vehicle", None))
    assert len(session.calls) == 3
    assert session.calls[1][2]["SelectedPermit"] == "fixture-permit"
    assert "PermitId" not in session.calls[1][2]
    assert session.calls[2][2]["VrmSelected"] == "fixture-vehicle"
    assert session.calls[2][2]["UseNewVrm"] == "False"
    assert result.registration == "AA00AAA"
    assert result.street == "Fixture Street"
    assert result.permit == "Fixture Visitor Permit"
    assert result.session_type == "DAY"
    assert result.starts_at == datetime(2026, 11, 13, 10, 5, tzinfo=ZoneInfo("Europe/London"))
    assert result.ends_at == datetime(2026, 11, 13, 23, 59, tzinfo=ZoneInfo("Europe/London"))
    assert result.form.fields["__RequestVerificationToken"] == "fixture-token-step-two"


def test_prepare_new_text_wins_and_normalizes_registration():
    session = Session(PERMIT, STEP, new_summary("AB12CD"))
    result = prepare(session, BookingInput("fixture-vehicle", " ab 12 cd "))
    fields = session.calls[2][2]
    assert fields["UseNewVrm"] == "True"
    assert fields["Vrm"] == "AB12CD"
    assert fields["VrmSelected"] == ""
    assert result.registration == "AB12CD"


@pytest.mark.parametrize("registration", ["", " ", "ABCDEFGHIJK", "AB-12CD", "AB_12CD"])
def test_prepare_invalid_registration_fails_before_http(registration):
    session = Session()
    with pytest.raises(ValueError):
        prepare(session, BookingInput(None, registration))
    assert session.calls == []


@pytest.mark.parametrize("save,nickname,expected_save,expected_name", [
    (True, "Family car", "True", "Family car"),
    (True, None, "True", ""),
    (False, "Hidden name", "False", ""),
])
def test_prepare_save_fields_follow_visible_new_registration_choice(save, nickname, expected_save, expected_name):
    session = Session(PERMIT, STEP, new_summary(save=save, nickname=expected_name))
    prepare(session, BookingInput(None, "BB00BBB", save, nickname))
    assert session.calls[2][2]["SaveThisVrm"] == expected_save
    assert session.calls[2][2]["VehicleKnownAs"] == expected_name


def test_prepare_prompt_uses_fresh_token_once():
    session = Session(PERMIT, STEP, PROMPT, new_summary())
    prepare(session, BookingInput(None, "BB00BBB"))
    assert len(session.calls) == 4
    assert session.calls[3][2]["__RequestVerificationToken"] == "fixture-token-confirm"
    assert session.calls[3][2]["ConfirmModal"] == "True"
    assert session.calls[3][2]["StreetSelected"] == "fixture-street"
    assert session.calls[3][2]["StartParkingNow"] == "True"


def test_prepare_prompt_preserves_save_and_nickname():
    prompt = PROMPT.replace('name="SaveThisVrm" value="False"', 'name="SaveThisVrm" value="True"').replace('name="VehicleKnownAs" value=""', 'name="VehicleKnownAs" value="Family car"')
    summary = new_summary(save=True, nickname="Family car")
    session = Session(PERMIT, STEP, prompt, summary)
    prepare(session, BookingInput(None, "BB00BBB", True, "Family car"))
    assert session.calls[3][2]["SaveThisVrm"] == "True"
    assert session.calls[3][2]["VehicleKnownAs"] == "Family car"


@pytest.mark.parametrize("field,old,new", [
    ("SaveThisVrm", "False", "True"),
    ("StreetSelected", "fixture-street", "other-street"),
    ("StartParkingNow", "True", "False"),
    ("VehicleKnownAs", 'name="VehicleKnownAs" value=""', ''),
])
def test_prepare_prompt_rejects_changed_or_missing_choice(field, old, new):
    if field == "VehicleKnownAs":
        prompt = PROMPT.replace(old, new)
    else:
        prompt = PROMPT.replace(f'name="{field}" value="{old}"', f'name="{field}" value="{new}"')
    session = Session(PERMIT, STEP, prompt)
    with pytest.raises(UnexpectedPage):
        prepare(session, BookingInput(None, "BB00BBB"))
    assert len(session.calls) == 3


@pytest.mark.parametrize("prompt", [
    PROMPT.replace("BB00BBB?", "CC00CCC?"),
    PROMPT.replace("Are you sure you wish to book parking for BB00BBB?", "Accept new terms?"),
])
def test_prepare_changed_prompt_fails_without_confirmation(prompt):
    session = Session(PERMIT, STEP, prompt)
    with pytest.raises(UnexpectedPage):
        prepare(session, BookingInput(None, "BB00BBB"))
    assert len(session.calls) == 3


def test_prepare_repeated_prompt_stops_after_one_confirmation():
    session = Session(PERMIT, STEP, PROMPT, PROMPT)
    with pytest.raises(UnexpectedPage):
        prepare(session, BookingInput(None, "BB00BBB"))
    assert len(session.calls) == 4


@pytest.mark.parametrize("old,new", [
    ("AA00AAA", "CC00CCC"),
    ("at Fixture Street", "at Other Street"),
    (">Fixture Visitor Permit</div>", ">Other Permit</div>"),
    ("VisitorSessionType\" value=\"DAY", "VisitorSessionType\" value=\"NIGHT"),
    ("10:05", "11:05"),
    ("10:05", "10:11"),
    ("23:59", "23:58"),
    ("23:59 today", "23:59 tomorrow"),
])
def test_prepare_rejects_changed_step_two_summary(old, new):
    session = Session(PERMIT, STEP, SUMMARY.replace(old, new))
    with pytest.raises(UnexpectedPage):
        prepare(session, BookingInput("fixture-vehicle", None))
    assert len(session.calls) == 3


def test_prepare_rebinds_current_permit_and_street_ids_by_labels():
    permit = PERMIT.replace('name="SelectedPermit" value="fixture-permit"', 'name="SelectedPermit" value="new-permit"')
    step = STEP.replace('Value="fixture-street"', 'Value="new-street"')
    summary = SUMMARY.replace('name="StreetSelected" value="fixture-street"', 'name="StreetSelected" value="new-street"')
    session = Session(permit, step, summary)

    result = prepare(session, BookingInput("fixture-vehicle", None))

    assert session.calls[1][2]["SelectedPermit"] == "new-permit"
    assert session.calls[2][2]["StreetSelected"] == "new-street"
    assert result.street == "Fixture Street"
    assert result.permit == "Fixture Visitor Permit"


@pytest.mark.parametrize("permit,expected_code", [
    (PERMIT.replace('<p>Visitor</p>', '<p>Other</p>'), "permit_changed"),
    (PERMIT.replace('</tbody>', '''<tr class="Display"><td class="mobile-list-description">
    <p>Fixture</p><p>Visitor</p><p>Permit</p></td><td class="list-button">
    <form method="post" action="/LeedsCC/BookParking/StepOne">
    <input type="hidden" name="SelectedPermit" value="other-permit" />
    <input type="hidden" name="__RequestVerificationToken" value="other-token" />
    </form></td></tr></tbody>'''), "permit_changed"),
])
def test_prepare_rejects_missing_or_ambiguous_permit_label_before_post(permit, expected_code):
    session = Session(permit)
    with pytest.raises(UnexpectedPage) as error:
        prepare(session, BookingInput("fixture-vehicle", None))
    assert error.value.code == expected_code
    assert len(session.calls) == 1


@pytest.mark.parametrize("step", [
    STEP.replace('<span>Fixture Street</span>', '<span>Other Street</span>'),
    STEP.replace('<span>Fixture Street</span>', '<span>Fixture Street</span><span>Other Street</span>'),
])
def test_prepare_rejects_missing_or_ambiguous_street_label_before_booking_post(step):
    session = Session(PERMIT, step)
    with pytest.raises(UnexpectedPage):
        prepare(session, BookingInput("fixture-vehicle", None))
    assert len(session.calls) == 2


SUCCESS = (FIXTURES / "success.html").read_text()
SUCCESS_URL = "https://visitor.paysmarti.co.uk/LeedsCC/BookParkingSuccess"


def commit_session(*post_results):
    session = Session(PERMIT, STEP, SUMMARY, *post_results)
    booker = PaysmartiBooker(session, CONTEXT)
    prepared = asyncio.run(booker.prepare(BookingInput("fixture-vehicle", None)))
    return booker, prepared, session


def success_redirect(location="/LeedsCC/BookParkingSuccess"):
    return Response("", 302, {"Location": location})


def test_commit_posts_step_two_once_and_verifies_success_page():
    booker, prepared, session = commit_session(success_redirect(), Response(SUCCESS))

    receipt = asyncio.run(booker.commit(prepared))

    assert len(session.calls) == 5
    assert session.calls[3] == (
        "POST", "https://visitor.paysmarti.co.uk/LeedsCC/BookParking/StepTwo",
        prepared.form.fields, False, 30,
    )
    assert session.calls[4] == ("GET", SUCCESS_URL, None, False, 30)
    assert receipt.registration == "AA00AAA"
    assert receipt.starts_at == datetime(2026, 11, 13, 10, 5, tzinfo=ZoneInfo("Europe/London"))
    assert receipt.ends_at == datetime(2026, 11, 13, 23, 59, tzinfo=ZoneInfo("Europe/London"))
    assert receipt.reference == "FIXTURE-BOOKING"


@pytest.mark.parametrize("result", [
    TimeoutError("timed out"),
    Response("error", 500),
    success_redirect("https://other.example/LeedsCC/BookParkingSuccess"),
    success_redirect("/LeedsCC/BookParkingSuccess?next=1"),
    success_redirect("/LeedsCC/BookParking/StepTwo"),
    Response(SUCCESS),
])
def test_commit_uncertain_post_never_resubmits(result):
    booker, prepared, session = commit_session(result)

    with pytest.raises(OutcomeUnknown):
        asyncio.run(booker.commit(prepared))
    with pytest.raises(OutcomeUnknown):
        asyncio.run(booker.commit(prepared))
    assert len(session.calls) == 4


@pytest.mark.parametrize("success", [
    SUCCESS.replace("AA00AAA", "BB00BBB"),
    SUCCESS.replace("10:05", "10:06"),
    SUCCESS.replace("23:59", "23:58"),
    SUCCESS.replace("Parking booked", "Parking pending"),
    SUCCESS.replace("Registration: AA00AAA", "Registration: "),
])
def test_commit_changed_success_page_is_unknown(success):
    booker, prepared, session = commit_session(success_redirect(), Response(success))

    with pytest.raises(OutcomeUnknown):
        asyncio.run(booker.commit(prepared))
    assert len(session.calls) == 5


def test_commit_broken_success_get_is_unknown_without_resubmission():
    booker, prepared, session = commit_session(success_redirect(), TimeoutError("timed out"))

    with pytest.raises(OutcomeUnknown):
        asyncio.run(booker.commit(prepared))
    assert len(session.calls) == 5


def test_commit_duplicate_click_after_success_does_not_post_again():
    booker, prepared, session = commit_session(success_redirect(), Response(SUCCESS))
    asyncio.run(booker.commit(prepared))

    with pytest.raises(OutcomeUnknown):
        asyncio.run(booker.commit(prepared))
    assert len(session.calls) == 5


def test_commit_copied_preparation_and_second_booker_cannot_post_again():
    booker, prepared, session = commit_session(success_redirect(), Response(SUCCESS))
    copied = replace(prepared)
    asyncio.run(booker.commit(prepared))

    with pytest.raises(OutcomeUnknown):
        asyncio.run(booker.commit(copied))
    with pytest.raises(OutcomeUnknown):
        asyncio.run(PaysmartiBooker(session, CONTEXT).commit(copied))
    assert len(session.calls) == 5


class BlockingResponse(Response):
    def __init__(self, body, status, headers, entered, release):
        super().__init__(body, status, headers)
        self.entered = entered
        self.release = release

    async def __aenter__(self):
        self.entered.set()
        await self.release.wait()
        return self


def test_commit_concurrent_copy_cannot_post_twice():
    entered, release = asyncio.Event(), asyncio.Event()
    booker, prepared, session = commit_session(
        BlockingResponse("", 302, {"Location": "/LeedsCC/BookParkingSuccess"}, entered, release),
        Response(SUCCESS),
    )

    async def scenario():
        first = asyncio.create_task(booker.commit(prepared))
        await entered.wait()
        with pytest.raises(OutcomeUnknown):
            await PaysmartiBooker(session, CONTEXT).commit(replace(prepared))
        release.set()
        assert (await first).registration == "AA00AAA"
        assert len(session.calls) == 5

    asyncio.run(scenario())


def test_commit_cancellation_after_post_starts_is_unknown_and_blocks_retry():
    entered, release = asyncio.Event(), asyncio.Event()
    booker, prepared, session = commit_session(
        BlockingResponse("", 302, {"Location": "/LeedsCC/BookParkingSuccess"}, entered, release),
    )

    async def scenario():
        first = asyncio.create_task(booker.commit(prepared))
        await entered.wait()
        first.cancel()
        with pytest.raises(OutcomeUnknown):
            await first
        with pytest.raises(OutcomeUnknown):
            await PaysmartiBooker(session, CONTEXT).commit(replace(prepared))
        assert len(session.calls) == 4

    asyncio.run(scenario())


def test_commit_rejects_invalid_prepared_action_before_post():
    from dataclasses import replace

    booker, prepared, session = commit_session()
    invalid = replace(prepared, form=replace(prepared.form, action="https://other.example/StepTwo"))

    with pytest.raises(BookingRejected):
        asyncio.run(booker.commit(invalid))
    assert len(session.calls) == 3
