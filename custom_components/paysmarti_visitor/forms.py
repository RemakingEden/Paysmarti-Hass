"""Extract the current Paysmarti form without retaining response bodies."""

from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from .models import FormSnapshot, PermitOption, UnexpectedPage

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
    soup = BeautifulSoup(html, "html.parser", on_duplicate_attribute="ignore")
    matches = [
        form
        for form in soup.find_all("form")
        if urlsplit(str(form.get("action", ""))).path == expected_path
    ]
    if len(matches) != 1:
        raise UnexpectedPage("form", expected_path)
    return _snapshot(matches[0], expected_path)


def _snapshot(form: Tag, expected_path: str) -> FormSnapshot:
    fields = _fields(form)
    if not fields.get(_TOKEN):
        raise UnexpectedPage("form", expected_path, tuple(sorted(fields)), "token_missing")
    return FormSnapshot(action=str(form.get("action")), fields=fields)


def permit_forms(html: str, expected_action: str) -> tuple[tuple[PermitOption, FormSnapshot], ...]:
    """Pair each visible permit row's label with its own fresh post form."""
    expected_path = urlsplit(expected_action).path
    soup = BeautifulSoup(html, "html.parser", on_duplicate_attribute="ignore")
    pairs = []
    seen: set[str] = set()
    for form in soup.find_all("form"):
        if urlsplit(str(form.get("action", ""))).path != expected_path:
            continue
        row = form.find_parent("tr")
        if row is None or "Display" not in row.get("class", ()) or row.has_attr("hidden"):
            continue
        snapshot = _snapshot(form, expected_path)
        permit_id = snapshot.fields.get("SelectedPermit", "").strip()
        descriptions = row.select("td.mobile-list-description")
        if len(descriptions) != 1:
            raise UnexpectedPage("permit", expected_path)
        parts = [p.get_text(" ", strip=True) for p in descriptions[0].find_all("p", recursive=False)]
        if not permit_id or len(parts) != 3 or any(not part for part in parts) or permit_id in seen:
            raise UnexpectedPage("permit", expected_path)
        seen.add(permit_id)
        label = " ".join(" ".join(parts).split())
        pairs.append((PermitOption(permit_id, label), snapshot))
    return tuple(pairs)


def form_options(html: str, expected_action: str, field: str) -> tuple[tuple[str, str], ...]:
    """Read all nonempty choices for one control on the token-bearing form."""
    expected_path = urlsplit(expected_action).path
    soup = BeautifulSoup(html, "html.parser", on_duplicate_attribute="ignore")
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
    elif control.name == "input" and control.get("type") == "hidden" and field == "StreetSelected":
        parent = control.parent
        labels = [span.get_text(" ", strip=True) for span in parent.find_all("span", recursive=False)]
        labels = [label for label in labels if label]
        if len(labels) != 1:
            raise UnexpectedPage("form", expected_path, (field,))
        options = [(str(control.get("value", "")).strip(), labels[0])]
    else:
        raise UnexpectedPage("form", expected_path, (field,))
    if any(value and not label for value, label in options):
        raise UnexpectedPage("form", expected_path, (field,))
    valid = [(value, label) for value, label in options if value]
    if len({value for value, _ in valid}) != len(valid):
        raise UnexpectedPage("form", expected_path, (field,))
    return tuple(valid)
