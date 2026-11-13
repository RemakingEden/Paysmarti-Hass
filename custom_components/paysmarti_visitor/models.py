"""Typed contracts shared by the Paysmarti visitor flow."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FormSnapshot:
    action: str
    fields: dict[str, str]


class UnexpectedPage(Exception):
    """A page differs from the observed flow; values remain outside diagnostics."""

    def __init__(
        self,
        stage: str,
        path: str,
        field_names: tuple[str, ...] = (),
        code: str = "form_changed",
    ) -> None:
        self.stage = stage
        self.path = path
        self.field_names = field_names
        self.code = code
        super().__init__(f"{code} at {stage}")


class AuthenticationFailed(Exception):
    """Credentials no longer establish a Paysmarti session."""

    code = "authentication_failed"


class InteractiveChallenge(Exception):
    """Login requires a person to complete an additional challenge."""

    code = "login_challenge"


class UnexpectedRedirect(Exception):
    """Authentication attempted to leave the trusted HTTPS host set."""

    code = "unexpected_redirect"


@dataclass(frozen=True)
class BookingInput:
    saved_vehicle_id: str | None
    new_registration: str | None
    save: bool = False
    nickname: str | None = None


@dataclass(frozen=True)
class BookingReceipt:
    registration: str
    starts_at: datetime
    ends_at: datetime
    reference: str | None = None


@dataclass(frozen=True)
class VehicleOption:
    id: str
    registration: str
    label: str


@dataclass(frozen=True)
class PermitOption:
    id: str
    label: str


@dataclass(frozen=True)
class StreetOption:
    id: str
    label: str
