"""Prepare a visitor booking and stop before the final, state-changing POST."""

import re
from datetime import datetime, timedelta, time
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .auth import VISITOR_URL
from .const import VISITOR_HOST
from .forms import form_options, parse_form
from .models import BookingContext, BookingInput, FormSnapshot, PreparedBooking, UnexpectedPage

_STEP_ONE = "/LeedsCC/BookParking/StepOne"
_STEP_TWO = "/LeedsCC/BookParking/StepTwo"
_LOCAL_ZONE = ZoneInfo("Europe/London")
_PARKING = re.compile(
    r"The total charge for parking (?P<registration>[A-Z0-9 ]+)"
    r"(?: \([^()]*\))? at (?P<street>.+?) from "
    r"(?P<start>\d{1,2}:\d{2}) to (?P<end>\d{1,2}:\d{2}) today is £\d+\.\d{2}\s*\."
)
_PRESERVED_FIELDS = (
    "UseNewVrm", "Vrm", "VrmSelected", "StreetSelected", "StartParkingNow",
    "ParkMultipleVehicles", "SaveThisVrm", "VehicleKnownAs", "VisitorSessionType",
)
_BOOLEAN_FIELDS = {"UseNewVrm", "StartParkingNow", "ParkMultipleVehicles", "SaveThisVrm"}


def _now() -> datetime:
    return datetime.now(_LOCAL_ZONE)


def _trusted_action(action: str, path: str) -> str:
    url = urljoin(VISITOR_URL, action)
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != VISITOR_HOST or parts.path != path
            or parts.username or parts.password or parts.port not in (None, 443)
            or parts.query or parts.fragment):
        raise UnexpectedPage("booking", path)
    return url


async def _page(session, method: str, url: str, fields: dict[str, str] | None = None) -> str:
    async with session.request(method, url, data=fields, allow_redirects=False, timeout=30) as response:
        if response.status != 200:
            raise UnexpectedPage("booking", urlsplit(url).path)
        return await response.text()


def _single_option(html: str, field: str, expected_id: str, expected_label: str) -> None:
    options = form_options(html, _STEP_ONE, field)
    if sum(option == (expected_id, expected_label) for option in options) != 1:
        raise UnexpectedPage(field, _STEP_ONE, code=f"{field}_changed")


def _permit_summary(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    matches = []
    for label in soup.find_all("label"):
        if label.get_text(" ", strip=True) == "Permit" and label.parent:
            values = label.parent.select("div.form-control-static")
            if len(values) == 1:
                matches.append(values[0].get_text(" ", strip=True))
    if len(matches) != 1 or not matches[0]:
        raise UnexpectedPage("summary", _STEP_TWO, code="summary_changed")
    return matches[0]


def _preserved(current: dict[str, str], expected: dict[str, str], stage: str, path: str) -> None:
    for field in _PRESERVED_FIELDS:
        actual, wanted = current.get(field), expected[field]
        if actual is None or (actual.lower() if field in _BOOLEAN_FIELDS else actual) != (wanted.lower() if field in _BOOLEAN_FIELDS else wanted):
            raise UnexpectedPage(stage, path, code=f"{field}_changed")


def _verified_summary(html: str, context: BookingContext, registration: str, expected_fields: dict[str, str]) -> PreparedBooking:
    form = parse_form(html, _STEP_TWO)
    _trusted_action(form.action, _STEP_TWO)
    _preserved(form.fields, expected_fields, "summary", _STEP_TWO)
    if _permit_summary(html) != context.permit_label:
        raise UnexpectedPage("summary", _STEP_TWO, code="permit_changed")
    soup = BeautifulSoup(html, "html.parser")
    matches = [_PARKING.fullmatch(p.get_text(" ", strip=True)) for p in soup.find_all("p")]
    matches = [match for match in matches if match]
    if len(matches) != 1:
        raise UnexpectedPage("summary", _STEP_TWO, code="parking_changed")
    match = matches[0]
    if re.sub(r"\s+", "", match["registration"]).upper() != registration:
        raise UnexpectedPage("summary", _STEP_TWO, code="registration_changed")
    if match["street"] != context.street_label:
        raise UnexpectedPage("summary", _STEP_TWO, code="street_changed")
    try:
        start_time = datetime.strptime(match["start"], "%H:%M").time()
        end_time = datetime.strptime(match["end"], "%H:%M").time()
    except ValueError as exc:
        raise UnexpectedPage("summary", _STEP_TWO, code="parking_changed") from exc
    now = _now().astimezone(_LOCAL_ZONE)
    start = datetime.combine(now.date(), start_time, _LOCAL_ZONE)
    end = datetime.combine(now.date(), end_time, _LOCAL_ZONE)
    if start > now or start < now - timedelta(minutes=5) or end_time != time(23, 59):
        raise UnexpectedPage("summary", _STEP_TWO, code="parking_changed")
    return PreparedBooking(form, registration, context.street_label, context.permit_label, start, end, "DAY")


def _is_prompt(html: str, registration: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    scripts = [script.get_text() for script in soup.find_all("script")]
    dialogs = [script for script in scripts if "bootbox.dialog" in script]
    if len(dialogs) != 1 or dialogs[0].count("bootbox.dialog") != 1:
        return False
    message = re.search(r"bootbox\.dialog\s*\(\s*\{\s*message\s*:\s*(['\"])(.*?)\1", dialogs[0], re.S)
    return bool(message and message.group(2) == f"Are you sure you wish to book parking for {registration}?")


class PaysmartiBooker:
    def __init__(self, session, context: BookingContext) -> None:
        self._session = session
        self._context = context

    async def prepare(self, request: BookingInput) -> PreparedBooking:
        """Verify the current forms and summary; never submit Step Two."""
        new = re.sub(r"\s+", "", request.new_registration or "").upper()
        if request.new_registration is not None and not new and not request.saved_vehicle_id:
            raise ValueError("Registration required")
        if new and re.fullmatch(r"[A-Z0-9]{1,10}", new) is None:
            raise ValueError("Invalid registration")
        if not new and not request.saved_vehicle_id:
            raise ValueError("Registration required")

        permit_html = await _page(self._session, "GET", VISITOR_URL)
        permit_form = parse_form(permit_html, _STEP_ONE)
        _single_option(permit_html, "PermitId", self._context.permit_id, self._context.permit_label)
        step_html = await _page(self._session, "POST", _trusted_action(permit_form.action, _STEP_ONE),
                                {**permit_form.fields, "PermitId": self._context.permit_id})
        step_form = parse_form(step_html, _STEP_ONE)
        _single_option(step_html, "StreetSelected", self._context.street_id, self._context.street_label)
        if step_form.fields.get("VisitorSessionType") != "DAY":
            raise UnexpectedPage("booking", _STEP_ONE, code="session_changed")
        if new:
            registration = new
            selected = ""
        else:
            options = form_options(step_html, _STEP_ONE, "VrmSelected")
            matches = [label for value, label in options if value == request.saved_vehicle_id]
            if len(matches) != 1:
                raise UnexpectedPage("vehicle", _STEP_ONE, code="vehicle_changed")
            registration = re.sub(r"\s+", "", matches[0]).upper()
            if re.fullmatch(r"[A-Z0-9]{1,10}", registration) is None or not any(c.isdigit() for c in registration):
                raise UnexpectedPage("vehicle", _STEP_ONE, code="vehicle_unverifiable")
            selected = request.saved_vehicle_id or ""
        fields = {
            **step_form.fields,
            "VrmSelected": selected,
            "UseNewVrm": str(bool(new)),
            "Vrm": new,
            "SaveThisVrm": str(bool(new and request.save)),
            "VehicleKnownAs": (request.nickname or "").strip() if new and request.save else "",
            "StreetSelected": self._context.street_id,
            "VisitorSessionType": "DAY",
            "ParkMultipleVehicles": "False",
            "StartParkingNow": "True",
        }
        result = await _page(self._session, "POST", _trusted_action(step_form.action, _STEP_ONE), fields)
        try:
            return _verified_summary(result, self._context, registration, fields)
        except UnexpectedPage:
            if not new or not _is_prompt(result, registration):
                raise
        prompt_form = parse_form(result, _STEP_ONE)
        _preserved(prompt_form.fields, fields, "prompt", _STEP_ONE)
        if (prompt_form.fields.get("__RequestVerificationToken") == step_form.fields.get("__RequestVerificationToken")
                or prompt_form.fields.get("ConfirmModal") != "False"
                ):
            raise UnexpectedPage("prompt", _STEP_ONE, code="prompt_changed")
        confirmed = await _page(self._session, "POST", _trusted_action(prompt_form.action, _STEP_ONE),
                                {**prompt_form.fields, "ConfirmModal": "True"})
        return _verified_summary(confirmed, self._context, registration, fields)
