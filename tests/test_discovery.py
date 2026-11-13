"""Current-form discovery against sanitized captured form structure."""

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
SECOND_ROW = '''<tr class="Display"><td class="mobile-list-description">
<p>Other</p><p>Visitor</p><p>Permit</p></td><td class="list-button">
<form method="post" action="/LeedsCC/BookParking/StepOne">
<input type="hidden" name="SelectedPermit" value="other-permit" />
<input type="hidden" name="__RequestVerificationToken" value="other-token" />
</form></td></tr>'''


class Response:
    def __init__(self, body):
        self.status = 200
        self.headers = {}
        self.body = body

    async def text(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class Session:
    def __init__(self, permit=PERMIT, step=STEP):
        self.responses = deque((Response(permit), Response(step)))
        self.calls = []

    def request(self, method, url, *, data=None, allow_redirects=True, timeout=None):
        self.calls.append((method, url, data, allow_redirects, timeout))
        return self.responses.popleft()


def run(coro):
    return asyncio.run(coro)


def with_second_permit(permit=PERMIT):
    return permit.replace('</tbody>', SECOND_ROW + '</tbody>')


def test_single_hidden_permit_and_street_use_current_form_values():
    session = Session()
    context = run(discover_context(session, None, None))
    assert (context.permit_id, context.permit_label) == ("fixture-permit", "Fixture Visitor Permit")
    assert (context.street_id, context.street_label) == ("fixture-street", "Fixture Street")
    assert session.calls[0][:2] == ("GET", VISITOR)
    assert session.calls[1][:2] == ("POST", STEP_ONE)
    assert session.calls[1][2]["SelectedPermit"] == "fixture-permit"
    assert "PermitId" not in session.calls[1][2]
    assert session.calls[1][2]["__RequestVerificationToken"] == "fixture-token-permit"
    assert all(call[3] is False and 0 < call[4] <= 30 for call in session.calls)


@pytest.mark.parametrize("permit,step,code", [
    (PERMIT.replace('<tr class="Display">', '<tr class="NoDisplay">'), STEP, "permit_missing"),
    (PERMIT, STEP.replace('Value="fixture-street" name="StreetSelected" value=""', 'Value="" name="StreetSelected" value=""'), "street_missing"),
])
def test_no_current_option_fails_closed(permit, step, code):
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(permit, step), None, None))
    assert error.value.code == code


def test_two_permit_rows_require_explicit_setup_choice():
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(with_second_permit()), None, None))
    assert error.value.code == "permit_ambiguous"
    assert [(option.id, option.label) for option in error.value.options] == [
        ("fixture-permit", "Fixture Visitor Permit"), ("other-permit", "Other Visitor Permit")
    ]


def test_configured_permit_selects_its_own_token_and_hidden_id():
    session = Session(with_second_permit())
    context = run(discover_context(session, "Other Visitor Permit", None))
    assert context.permit_id == "other-permit"
    assert session.calls[1][2]["SelectedPermit"] == "other-permit"
    assert session.calls[1][2]["__RequestVerificationToken"] == "other-token"


@pytest.mark.parametrize("expected_permit,expected_street,code", [
    ("Disappeared Permit", None, "permit_changed"),
    (None, "Disappeared Street", "street_changed"),
])
def test_configured_label_disappearing_fails_closed(expected_permit, expected_street, code):
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(), expected_permit, expected_street))
    assert error.value.code == code


def test_multiple_visible_street_labels_fail_closed():
    step = STEP.replace('<span>Fixture Street</span>', '<span>Fixture Street</span><span>Other Street</span>')
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(step=step), None, None))
    assert error.value.code == "form_changed"


def test_missing_visible_street_label_fails_closed():
    step = STEP.replace('<span>Fixture Street</span>', '<span></span>')
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(step=step), None, None))
    assert error.value.code == "form_changed"


def test_empty_saved_vehicle_list_is_valid():
    context = run(discover_context(Session(), None, None))
    empty = STEP.replace('<option value="fixture-vehicle" selected>AA00AAA (Fixture)</option>', '')
    assert run(list_saved_vehicles(Session(step=empty), context)) == []


def test_saved_vehicles_refresh_from_current_option_label():
    context = run(discover_context(Session(), None, None))
    changed = STEP.replace('fixture-vehicle" selected>AA00AAA (Fixture)', 'new-vehicle" selected>CC00CCC (Family car)')
    assert run(list_saved_vehicles(Session(step=changed), context)) == [
        VehicleOption("new-vehicle", "CC00CCC", "CC00CCC (Family car)")
    ]


def test_vehicle_refresh_rejects_changed_street():
    context = run(discover_context(Session(), None, None))
    changed = STEP.replace('<span>Fixture Street</span>', '<span>Other Street</span>')
    with pytest.raises(UnexpectedPage) as error:
        run(list_saved_vehicles(Session(step=changed), context))
    assert error.value.code == "street_changed"


def test_configured_label_uses_current_hidden_ids():
    permit = PERMIT.replace('name="SelectedPermit" value="fixture-permit"', 'name="SelectedPermit" value="new-permit"')
    step = STEP.replace('Value="fixture-street" name="StreetSelected" value=""', 'Value="new-street" name="StreetSelected" value=""')
    context = run(discover_context(Session(permit, step), "Fixture Visitor Permit", "Fixture Street"))
    assert (context.permit_id, context.street_id) == ("new-permit", "new-street")


def test_duplicate_permit_labels_fail_closed():
    duplicate = SECOND_ROW.replace('<p>Other</p>', '<p>Fixture</p>')
    permit = PERMIT.replace('</tbody>', duplicate + '</tbody>')
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(permit), "Fixture Visitor Permit", None))
    assert error.value.code == "permit_changed"


def test_blank_permit_row_label_is_not_dropped():
    permit = with_second_permit().replace('<p>Other</p>', '<p></p>')
    with pytest.raises(UnexpectedPage) as error:
        run(discover_context(Session(permit), None, None))
    assert error.value.code == "form_changed"


@pytest.mark.parametrize("label", [
    "AA00AAA (Family car) extra", "FAMILYCAR", "AB12CD (A (nested) name)", "AB12CD (Fixture) (Second)",
])
def test_unverifiable_vehicle_label_fails_closed(label):
    context = run(discover_context(Session(), None, None))
    step = STEP.replace('AA00AAA (Fixture)</option>', f'{label}</option>')
    with pytest.raises(UnexpectedPage) as error:
        run(list_saved_vehicles(Session(step=step), context))
    assert error.value.code == "vehicle_unverifiable"


@pytest.mark.parametrize("display,registration", [
    ("aa00 aaa (Family car)", "AA00AAA"),
    ("A 1 (Personalized)", "A1"),
    ("ABC 123 (Legacy)", "ABC123"),
    ("1 abc (Short)", "1ABC"),
])
def test_vehicle_registration_precedes_parenthesized_nickname(display, registration):
    context = run(discover_context(Session(), None, None))
    step = STEP.replace('AA00AAA (Fixture)</option>', f'{display}</option>')
    assert run(list_saved_vehicles(Session(step=step), context)) == [
        VehicleOption("fixture-vehicle", registration, display)
    ]
