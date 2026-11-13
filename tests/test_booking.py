"""Pre-commit booking tests against sanitized form responses."""

import asyncio
from collections import deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from custom_components.paysmarti_visitor.booking import PaysmartiBooker
from custom_components.paysmarti_visitor.models import BookingContext, BookingInput, UnexpectedPage


FIXTURES = Path(__file__).parent / "fixtures"
PERMIT = (FIXTURES / "permit.html").read_text()
STEP = (FIXTURES / "step_one_saved.html").read_text()
PROMPT = (FIXTURES / "step_one_new_prompt.html").read_text()
SUMMARY = (FIXTURES / "step_two.html").read_text()
CONTEXT = BookingContext("fixture-permit", "Fixture Visitor Permit", "fixture-street", "Fixture Street")
NOW = datetime(2026, 11, 13, 10, 10, tzinfo=ZoneInfo("Europe/London"))


class Response:
    status = 200
    headers = {}

    def __init__(self, body):
        self.body = body

    async def text(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class Session:
    def __init__(self, *bodies):
        self.responses = deque(Response(body) for body in bodies)
        self.calls = []

    def request(self, method, url, *, data=None, allow_redirects=True, timeout=None):
        self.calls.append((method, url, data, allow_redirects, timeout))
        return self.responses.popleft()


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
