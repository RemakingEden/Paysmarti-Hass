"""Prepare a visitor booking and confirm it with one final POST."""

import asyncio
import re
from datetime import datetime, timedelta, time
from urllib.parse import parse_qsl, urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .auth import VISITOR_URL
from .const import VISITOR_HOST
from .forms import form_options, parse_form, permit_forms
from .models import (
    BookingContext, BookingInput, BookingReceipt, BookingRejected, FormSnapshot,
    OutcomeUnknown, PreparedBooking, UnexpectedPage,
)

_STEP_ONE = "/LeedsCC/BookParking/StepOne"
_STEP_TWO = "/LeedsCC/BookParking/StepTwo"
_SUCCESS = "/LeedsCC/BookParkingSuccess"
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


def _trusted_success_url(location: str, permit_id: str) -> str:
    """Accept only the observed booking receipt redirect for this permit."""
    url = urljoin(VISITOR_URL, location)
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != VISITOR_HOST or parts.path != _SUCCESS
            or parts.username or parts.password or parts.port not in (None, 443) or parts.fragment):
        raise ValueError("Unexpected success redirect")
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ValueError("Invalid success redirect query") from exc
    parameters = dict(pairs)
    if (len(pairs) != 2 or set(parameters) != {"permit", "paymentId"}
            or parameters["permit"] != permit_id
            or not re.fullmatch(r"[0-9]+", parameters["paymentId"])):
        raise ValueError("Unexpected success redirect query")
    return url


async def _page(session, method: str, url: str, fields: dict[str, str] | None = None) -> str:
    async with session.request(method, url, data=fields, allow_redirects=False, timeout=30) as response:
        if response.status != 200:
            raise UnexpectedPage("booking", urlsplit(url).path)
        return await response.text()


def _current_option_id(html: str, field: str, expected_label: str) -> str:
    options = form_options(html, _STEP_ONE, field)
    matches = [option_id for option_id, label in options if label == expected_label]
    if len(matches) != 1:
        raise UnexpectedPage(field, _STEP_ONE, code=f"{field}_changed")
    return matches[0]


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


def _success_receipt(html: str, prepared: PreparedBooking) -> BookingReceipt:
    soup = BeautifulSoup(html, "html.parser")
    headings = [heading.get_text(" ", strip=True).lower() for heading in soup.find_all("h2")]
    if headings.count("book parking (step 3 of 3)") != 1:
        raise ValueError("Success step missing")
    messages = [paragraph.get_text(" ", strip=True) for paragraph in soup.find_all("p")]
    if messages.count("Your visitor parking session has been booked.") != 1:
        raise ValueError("Success message missing")

    def field(label: str) -> str:
        matches = []
        for element in soup.find_all("label"):
            if " ".join(element.get_text(" ", strip=True).split()) == label and element.parent:
                values = element.parent.select("div.form-control-static")
                if len(values) == 1:
                    matches.append(values[0].get_text(" ", strip=True))
        if len(matches) != 1 or not matches[0]:
            raise ValueError(f"Success {label} missing")
        return matches[0]

    if field("Permit") != prepared.permit or field("Location") != prepared.street:
        raise ValueError("Success permit or location differs")
    registration = re.sub(r"\s+", "", field("Vehicle registration number")).upper()
    if registration != prepared.registration:
        raise ValueError("Success registration differs")
    start = datetime.strptime(field("Parking allowed from"), "%H:%M %d/%m/%Y").replace(tzinfo=_LOCAL_ZONE)
    end = datetime.strptime(field("Parking allowed until"), "%H:%M %d/%m/%Y").replace(tzinfo=_LOCAL_ZONE)
    if start != prepared.starts_at or end != prepared.ends_at:
        raise ValueError("Success times differ")
    return BookingReceipt(registration, start, end)


class PaysmartiBooker:
    def __init__(self, session, context: BookingContext) -> None:
        self._session = session
        self._context = context

    async def commit(self, prepared: PreparedBooking) -> BookingReceipt:
        """Submit a prepared booking once; ambiguous post-submit results stay unknown."""
        if prepared._commit_gate.started:
            raise OutcomeUnknown("This prepared booking has already been submitted")
        try:
            url = _trusted_action(prepared.form.action, _STEP_TWO)
            if not prepared.form.fields.get("__RequestVerificationToken"):
                raise ValueError("Missing verification token")
        except (UnexpectedPage, ValueError) as exc:
            raise BookingRejected("Prepared booking cannot be submitted") from exc

        # This gate is shared by dataclass copies and checked before the first await.
        # Once set, cancellation is classified as unknown and cannot cause a resend.
        prepared._commit_gate.started = True
        try:
            async with self._session.request(
                "POST", url, data=prepared.form.fields, allow_redirects=False, timeout=30,
            ) as response:
                if response.status != 302:
                    raise ValueError("Unexpected final POST response")
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Success redirect missing")
                success_url = _trusted_success_url(location, self._context.permit_id)
            async with self._session.request(
                "GET", success_url, data=None, allow_redirects=False, timeout=30,
            ) as response:
                if response.status != 200:
                    raise ValueError("Success page unavailable")
                return _success_receipt(await response.text(), prepared)
        except asyncio.CancelledError as exc:
            raise OutcomeUnknown("Final booking was interrupted and could not be verified") from exc
        except Exception as exc:
            raise OutcomeUnknown("Final booking result could not be verified") from exc

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
        permit_matches = [form for option, form in permit_forms(permit_html, _STEP_ONE)
                          if option.label == self._context.permit_label]
        if len(permit_matches) != 1:
            raise UnexpectedPage("permit", _STEP_ONE, code="permit_changed")
        permit_form = permit_matches[0]
        step_html = await _page(self._session, "POST", _trusted_action(permit_form.action, _STEP_ONE),
                                permit_form.fields)
        step_form = parse_form(step_html, _STEP_ONE)
        street_id = _current_option_id(step_html, "StreetSelected", self._context.street_label)
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
            label = matches[0]
            registration_match = re.fullmatch(r"(.+?)(?:\s+\([^()]+\))?", label)
            if registration_match is None:
                raise UnexpectedPage("vehicle", _STEP_ONE, code="vehicle_unverifiable")
            registration = re.sub(r"\s+", "", registration_match.group(1)).upper()
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
            "StreetSelected": street_id,
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
