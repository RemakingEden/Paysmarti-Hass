"""Extract the current Paysmarti form without retaining response bodies."""

from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from .models import FormSnapshot, UnexpectedPage

_TOKEN = "__RequestVerificationToken"


def _fields(form: Tag) -> dict[str, str]:
    values: dict[str, str] = {}
    duplicates: set[str] = set()
    for control in form.select("input[name], select[name], textarea[name], button[name]"):
        if control.has_attr("disabled"):
            continue
        name = str(control.get("name"))
        if control.name == "input":
            kind = str(control.get("type", "text")).lower()
            if kind in ("radio", "checkbox") and not control.has_attr("checked"):
                continue
            value = str(control.get("value", ""))
        elif control.name == "select":
            option = control.find("option", selected=True) or control.find("option")
            value = str(option.get("value", option.get_text(strip=True))) if option else ""
        elif control.name == "textarea":
            value = control.get_text()
        else:
            value = str(control.get("value", control.get_text(strip=True)))
        if name in values:
            duplicates.add(name)
        values[name] = value
    if duplicates:
        raise UnexpectedPage("form", urlsplit(str(form.get("action", ""))).path, tuple(sorted(duplicates)))
    return values


def parse_form(html: str, expected_action: str) -> FormSnapshot:
    """Return one matching form and its current token, or fail closed.

    ``expected_action`` is a trusted path from the workflow, not a URL from
    the response. The caller separately validates an absolute action's host.
    """
    expected_path = urlsplit(expected_action).path
    soup = BeautifulSoup(html, "html.parser")
    matches = [
        form
        for form in soup.find_all("form")
        if urlsplit(str(form.get("action", ""))).path == expected_path
    ]
    if len(matches) != 1:
        raise UnexpectedPage("form", expected_path)
    form = matches[0]
    fields = _fields(form)
    if not fields.get(_TOKEN):
        raise UnexpectedPage("form", expected_path, tuple(sorted(fields)), "token_missing")
    return FormSnapshot(action=str(form.get("action")), fields=fields)


def form_options(html: str, expected_action: str, field: str) -> tuple[tuple[str, str], ...]:
    """Read all nonempty choices for one control on the token-bearing form."""
    expected_path = urlsplit(expected_action).path
    soup = BeautifulSoup(html, "html.parser")
    matches = [form for form in soup.find_all("form") if urlsplit(str(form.get("action", ""))).path == expected_path]
    if len(matches) != 1:
        raise UnexpectedPage("form", expected_path)
    form = matches[0]
    # The same form's current token is required even when reading choices.
    parse_form(html, expected_action)
    controls = form.select(f'[name="{field}"]')
    if len(controls) != 1:
        raise UnexpectedPage("form", expected_path, (field,))
    control = controls[0]
    if control.name == "select":
        options = [
            (str(option.get("value", "")).strip(), option.get_text(" ", strip=True))
            for option in control.find_all("option") if not option.has_attr("disabled")
        ]
    elif control.name == "input" and control.get("type") == "hidden":
        options = [(str(control.get("value", "")).strip(), str(control.get("data-label", "")).strip())]
    else:
        raise UnexpectedPage("form", expected_path, (field,))
    if any(value and not label for value, label in options):
        raise UnexpectedPage("form", expected_path, (field,))
    valid = [(value, label) for value, label in options if value]
    if len({value for value, _ in valid}) != len(valid):
        raise UnexpectedPage("form", expected_path, (field,))
    return tuple(valid)
