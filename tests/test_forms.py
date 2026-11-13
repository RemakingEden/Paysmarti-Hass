"""Paysmarti form shape tests using sanitized browser-flow fixtures."""

from pathlib import Path

import pytest

from custom_components.paysmarti_visitor.forms import parse_form
from custom_components.paysmarti_visitor.models import UnexpectedPage


FIXTURES = Path(__file__).parent / "fixtures"


def test_extracts_fresh_token_and_selected_fields() -> None:
    html = (FIXTURES / "step_one_saved.html").read_text()

    form = parse_form(html, "/LeedsCC/BookParking/StepOne")

    assert form.action == "/LeedsCC/BookParking/StepOne"
    assert form.fields["__RequestVerificationToken"] == "fixture-token-step-one"
    assert form.fields["CustomerId"] == "fixture-customer"
    assert form.fields["VrmSelected"] == "fixture-vehicle"
    assert form.fields["StreetSelected"] == "fixture-street"
    assert form.fields["Button"] == "Step1Continue"


def test_extracts_token_from_step_two_instead_of_reusing_step_one() -> None:
    html = (FIXTURES / "step_two.html").read_text()

    form = parse_form(html, "/LeedsCC/BookParking/StepTwo")

    assert form.fields["__RequestVerificationToken"] == "fixture-token-step-two"


@pytest.mark.parametrize("token_html", ["", '<input type="hidden" name="__RequestVerificationToken" value="" />'])
def test_rejects_missing_or_blank_token(token_html: str) -> None:
    html = f'<form action="/LeedsCC/BookParking/StepOne">{token_html}</form>'

    with pytest.raises(UnexpectedPage) as exc:
        parse_form(html, "/LeedsCC/BookParking/StepOne")

    assert exc.value.stage == "form"
    assert exc.value.path == "/LeedsCC/BookParking/StepOne"
    assert exc.value.code == "token_missing"


def test_rejects_wrong_action() -> None:
    html = (FIXTURES / "step_two.html").read_text()

    with pytest.raises(UnexpectedPage) as exc:
        parse_form(html, "/LeedsCC/BookParking/StepOne")

    assert exc.value.code == "form_changed"
    assert exc.value.path == "/LeedsCC/BookParking/StepOne"


def test_rejects_duplicate_token() -> None:
    html = '''<form action="/LeedsCC/BookParking/StepOne">
        <input name="__RequestVerificationToken" value="first" />
        <input name="__RequestVerificationToken" value="second" />
    </form>'''

    with pytest.raises(UnexpectedPage) as exc:
        parse_form(html, "/LeedsCC/BookParking/StepOne")

    assert exc.value.code == "form_changed"
    assert exc.value.field_names == ("__RequestVerificationToken",)


def test_does_not_pick_token_from_a_different_form() -> None:
    html = '''<form action="/other"><input name="__RequestVerificationToken" value="other" /></form>
        <form action="/LeedsCC/BookParking/StepOne"><input name="CustomerId" value="fixture" /></form>'''

    with pytest.raises(UnexpectedPage) as exc:
        parse_form(html, "/LeedsCC/BookParking/StepOne")

    assert exc.value.code == "token_missing"
