"""Current-form discovery with sanitized Paysmarti pages."""

import asyncio
from collections import deque
from pathlib import Path

import pytest

from custom_components.paysmarti_visitor.discovery import discover_context, list_saved_vehicles
from custom_components.paysmarti_visitor.models import UnexpectedPage, VehicleOption

FIXTURES = Path(__file__).parent / "fixtures"
VISITOR = "https://visitor.paysmarti.co.uk/LeedsCC/BookParking/SelectPermit"
STEP_ONE = "https://visitor.paysmarti.co.uk/LeedsCC/BookParking/StepOne"
PERMIT = (FIXTURES / "permit.html").read_text()
STEP = (FIXTURES / "step_one_saved.html").read_text()


class Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body
        self.headers = {}

    async def text(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class Session:
    def __init__(self, permit=PERMIT, step=STEP):
        self.responses = deque((Response(200, permit), Response(200, step)))
        self.calls = []

    def request(self, method, url, *, data=None, allow_redirects=True, timeout=None):
        self.calls.append((method, url, data, allow_redirects, timeout))
        return self.responses.popleft()


def run(coro):
    return asyncio.run(coro)


def test_single_permit_and_street_use_current_form_values():
    session = Session()
    context = run(discover_context(session, None, None))
    assert (context.permit_id, context.permit_label) == ("fixture-permit", "Fixture Visitor Permit")
    assert (context.street_id, context.street_label) == ("fixture-street", "Fixture Street")
    assert session.calls[0][:2] == ("GET", VISITOR)
    assert session.calls[1][:2] == ("POST", STEP_ONE)
    assert session.calls[1][2]["PermitId"] == "fixture-permit"
    assert session.calls[1][2]["__RequestVerificationToken"] == "fixture-token-permit"
    assert all(call[3] is False and 0 < call[4] <= 30 for call in session.calls)


@pytest.mark.parametrize("permit,step,code", [
    (PERMIT.replace('<option value="fixture-permit" selected>Fixture Visitor Permit</option>', ''), STEP, "permit_missing"),
    (PERMIT, STEP.replace('<option value="fixture-street" selected>Fixture Street</option>', ''), "street_missing"),
])
def test_no_option_fails_closed(permit, step, code):
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(permit, step), None, None))
    assert error.value.code == code


@pytest.mark.parametrize("permit,step,code", [
    (PERMIT.replace('</select>', '<option value="other-permit">Other Permit</option></select>'), STEP, "permit_ambiguous"),
    (PERMIT, STEP.replace('<option value="fixture-street" selected>Fixture Street</option>', '<option value="fixture-street" selected>Fixture Street</option><option value="other-street">Other Street</option>'), "street_ambiguous"),
])
def test_two_options_require_explicit_choice(permit, step, code):
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(permit, step), None, None))
    assert error.value.code == code
    assert len(error.value.options) == 2


@pytest.mark.parametrize("expected_permit,expected_street,code", [
    ("Disappeared Permit", None, "permit_changed"),
    (None, "Disappeared Street", "street_changed"),
])
def test_configured_label_disappearing_fails_closed(expected_permit, expected_street, code):
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(), expected_permit, expected_street))
    assert error.value.code == code


def test_empty_saved_vehicle_list_is_valid():
    context = run(discover_context(Session(), None, None))
    empty = STEP.replace('<option value="fixture-vehicle" selected>AA00AAA</option>', '')
    assert run(list_saved_vehicles(Session(step=empty), context)) == []


def test_saved_vehicles_refresh_from_current_form():
    context = run(discover_context(Session(), None, None))
    changed = STEP.replace('fixture-vehicle" selected>AA00AAA', 'new-vehicle" selected>CC00CCC')
    assert run(list_saved_vehicles(Session(step=changed), context)) == [VehicleOption("new-vehicle", "CC00CCC", "CC00CCC")]


def test_vehicle_refresh_rejects_changed_street():
    context = run(discover_context(Session(), None, None))
    changed = STEP.replace('>Fixture Street</option>', '>Other Street</option>')
    with pytest.raises(UnexpectedPage) as error:
        run(list_saved_vehicles(Session(step=changed), context))
    assert error.value.code == "street_changed"


def test_configured_label_selects_matching_option_without_using_old_id():
    permit = PERMIT.replace('fixture-permit" selected', 'new-permit" selected')
    step = STEP.replace('fixture-street" selected', 'new-street" selected')
    context = run(discover_context(Session(permit, step), "Fixture Visitor Permit", "Fixture Street"))
    assert (context.permit_id, context.street_id) == ("new-permit", "new-street")


def test_duplicate_labels_are_ambiguous_even_with_configured_label():
    permit = PERMIT.replace('</select>', '<option value="second-permit">Fixture Visitor Permit</option></select>')
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(permit), "Fixture Visitor Permit", None))
    assert error.value.code == "permit_changed"


def test_nonempty_permit_without_label_is_rejected_instead_of_auto_selecting_other():
    permit = PERMIT.replace('</select>', '<option value="unlabelled-permit"></option></select>')
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(permit), None, None))
    assert error.value.code == "form_changed"


def test_empty_value_placeholder_does_not_block_single_permit():
    permit = PERMIT.replace('<option value="fixture-permit"', '<option value="">Choose a permit</option><option value="fixture-permit"')
    context = run(discover_context(Session(permit), None, None))
    assert context.permit_id == "fixture-permit"


def test_saved_vehicle_with_descriptive_label_fails_closed():
    context = run(discover_context(Session(), None, None))
    step = STEP.replace('>AA00AAA</option>', '>AA00AAA (Family car)</option>')
    with pytest.raises(UnexpectedPage) as error:
        run(list_saved_vehicles(Session(step=step), context))
    assert error.value.code == "vehicle_unverifiable"


def test_saved_vehicle_with_long_bare_nickname_fails_closed():
    context = run(discover_context(Session(), None, None))
    step = STEP.replace('>AA00AAA</option>', '>FAMILYCARLONG</option>')
    with pytest.raises(UnexpectedPage) as error:
        run(list_saved_vehicles(Session(step=step), context))
    assert error.value.code == "vehicle_unverifiable"


@pytest.mark.parametrize("display,registration", [
    ("aa00 aaa", "AA00AAA"),
    ("A 1", "A1"),
    ("ABC 123", "ABC123"),
])
def test_saved_vehicle_normalizes_spaced_and_personalized_registrations(display, registration):
    context = run(discover_context(Session(), None, None))
    step = STEP.replace('>AA00AAA</option>', f'>{display}</option>')
    assert run(list_saved_vehicles(Session(step=step), context)) == [
        VehicleOption("fixture-vehicle", registration, display)
    ]


def test_saved_vehicle_with_short_bare_alphabetic_nickname_fails_closed():
    context = run(discover_context(Session(), None, None))
    step = STEP.replace('>AA00AAA</option>', '>FAMILYCAR</option>')
    with pytest.raises(UnexpectedPage) as error:
        run(list_saved_vehicles(Session(step=step), context))
    assert error.value.code == "vehicle_unverifiable"


@pytest.mark.parametrize("display,registration", [
    ("AB12CD", "AB12CD"),
    ("a 1", "A1"),
    ("1 abc", "1ABC"),
])
def test_saved_vehicle_accepts_short_alphanumeric_registrations_with_a_digit(display, registration):
    context = run(discover_context(Session(), None, None))
    step = STEP.replace('>AA00AAA</option>', f'>{display}</option>')
    assert run(list_saved_vehicles(Session(step=step), context)) == [
        VehicleOption("fixture-vehicle", registration, display)
    ]
