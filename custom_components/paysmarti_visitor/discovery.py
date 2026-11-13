"""Discover permit, fixed street, and saved vehicles from live visitor forms."""

import re
from urllib.parse import urljoin, urlsplit

from .auth import VISITOR_URL
from .const import VISITOR_HOST
from .forms import form_options, parse_form
from .models import BookingContext, DiscoveryChoiceRequired, PermitOption, StreetOption, UnexpectedPage, VehicleOption

_STEP_ONE = "/LeedsCC/BookParking/StepOne"


def _choice(options, expected: str | None, stage: str, path: str):
    if expected is not None:
        matches = [option for option in options if option.label == expected]
        if len(matches) != 1:
            raise UnexpectedPage(stage, path, code=f"{stage}_changed")
        return matches[0]
    if not options:
        raise UnexpectedPage(stage, path, code=f"{stage}_missing")
    if len(options) != 1:
        raise DiscoveryChoiceRequired(stage, path, tuple(options))
    return options[0]


def _trusted_action(action: str) -> str:
    target = urljoin(VISITOR_URL, action)
    parts = urlsplit(target)
    if parts.scheme != "https" or parts.hostname != VISITOR_HOST or parts.path != _STEP_ONE or parts.username or parts.password or parts.port not in (None, 443):
        raise UnexpectedPage("permit", _STEP_ONE)
    return target


async def _page(session, method: str, url: str, data: dict[str, str] | None = None) -> str:
    async with session.request(method, url, data=data, allow_redirects=False, timeout=30) as response:
        if response.status != 200:
            raise UnexpectedPage("discovery", urlsplit(url).path)
        return await response.text()


async def _discover(session, expected_permit: str | None, expected_street: str | None):
    permit_html = await _page(session, "GET", VISITOR_URL)
    permit_form = parse_form(permit_html, _STEP_ONE)
    permit_options = tuple(PermitOption(*item) for item in form_options(permit_html, _STEP_ONE, "PermitId"))
    permit = _choice(permit_options, expected_permit, "permit", urlsplit(VISITOR_URL).path)
    step_html = await _page(session, "POST", _trusted_action(permit_form.action), {**permit_form.fields, "PermitId": permit.id})
    parse_form(step_html, _STEP_ONE)
    street_options = tuple(StreetOption(*item) for item in form_options(step_html, _STEP_ONE, "StreetSelected"))
    street = _choice(street_options, expected_street, "street", _STEP_ONE)
    context = BookingContext(permit.id, permit.label, street.id, street.label)
    return context, step_html


async def discover_context(session, expected_permit: str | None, expected_street: str | None) -> BookingContext:
    """Bind unique choices or revalidate configured labels against current forms."""
    context, _ = await _discover(session, expected_permit, expected_street)
    return context


async def list_saved_vehicles(session, context: BookingContext) -> list[VehicleOption]:
    """Refresh the current Step One list after revalidating permit and street."""
    _, step_html = await _discover(session, context.permit_label, context.street_label)
    vehicles = []
    for value, label in form_options(step_html, _STEP_ONE, "VrmSelected"):
        # Spaces and case are display formatting. Anything else cannot safely
        # be split into a registration without a site-provided registration key.
        registration = re.sub(r"\s+", "", label).upper()
        if (
            re.fullmatch(r"[A-Z0-9]{1,10}", registration) is None
            or not any(char.isdigit() for char in registration)
        ):
            raise UnexpectedPage("vehicle", _STEP_ONE, code="vehicle_unverifiable")
        vehicles.append(VehicleOption(value, registration, label))
    return vehicles
