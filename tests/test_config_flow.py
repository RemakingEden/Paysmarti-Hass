"""Home Assistant account setup without any Paysmarti network calls."""

from types import SimpleNamespace

import pytest
import probatio
import aiohttp
from homeassistant.helpers.selector import TextSelector, TextSelectorType
from homeassistant.helpers.typing import UNDEFINED

from custom_components.paysmarti_visitor.models import (
    AuthenticationFailed, BookingContext, DiscoveryChoiceRequired,
    InteractiveChallenge, PermitOption, StreetOption,
)


class Session:
    def __init__(self):
        self.detached = False

    def detach(self):
        self.detached = True


class FakeAuthenticator:
    error = None
    logins = []

    def __init__(self, session, allowed_hosts):
        self.session = session

    async def login(self, email, password):
        self.logins.append((email, password))
        if self.error:
            raise self.error


class FakeHass:
    def __init__(self, entries=()):
        self.entries = list(entries)
        self.reloads = []
        self.config_entries = SimpleNamespace(
            async_entries=lambda domain: self.entries,
            async_get_entry=lambda entry_id: next((e for e in self.entries if e.entry_id == entry_id), None),
            async_get_known_entry=lambda entry_id: next(e for e in self.entries if e.entry_id == entry_id),
            async_entry_for_domain_unique_id=lambda domain, unique_id: next((e for e in self.entries if e.data["email"] == unique_id), None),
            flow=SimpleNamespace(async_progress_by_handler=lambda *args, **kwargs: []),
            async_update_entry=self.update_entry,
            async_reload=self.reload,
            async_schedule_reload=self.reloads.append,
        )

    def update_entry(self, entry, **changes):
        for key, value in changes.items():
            if value is not UNDEFINED:
                setattr(entry, key, value)
        return True

    async def reload(self, entry_id):
        self.reloads.append(entry_id)


@pytest.fixture
def setup(monkeypatch):
    from custom_components.paysmarti_visitor import config_flow

    sessions = []
    FakeAuthenticator.error = None
    FakeAuthenticator.logins = []
    monkeypatch.setattr(config_flow, "async_create_clientsession", lambda hass: sessions.append(Session()) or sessions[-1])
    monkeypatch.setattr(config_flow, "PaysmartiAuthenticator", FakeAuthenticator)
    context = BookingContext("p1", "Visitor", "s1", "Main Street")

    async def discover(session, permit, street):
        return context

    monkeypatch.setattr(config_flow, "discover_context", discover)
    return config_flow, sessions, context


def flow(module, hass, source="user", entry_id=None):
    result = module.PaysmartiVisitorConfigFlow()
    result.hass = hass
    result.context = {"source": source}
    if entry_id:
        result.context["entry_id"] = entry_id
    return result


@pytest.mark.asyncio
async def test_valid_login_binds_single_permit_and_street(setup):
    module, sessions, _ = setup
    configured = flow(module, FakeHass())
    form = await configured.async_step_user()
    password_field = next(value for key, value in form["data_schema"].schema.items() if key.schema == "password")
    assert isinstance(password_field, TextSelector)
    assert password_field.config["type"] == TextSelectorType.PASSWORD
    result = await configured.async_step_user({"email": "a@example.test", "password": "secret"})
    assert result["type"] == "create_entry"
    assert result["data"] == {
        "email": "a@example.test", "password": "secret",
        "permit_label": "Visitor", "street_label": "Main Street",
    }
    assert sessions[0].detached
    assert configured.context["unique_id"] == "a@example.test"


@pytest.mark.asyncio
async def test_multiple_choices_require_explicit_selection(setup, monkeypatch):
    module, _, context = setup
    async def discover(session, permit, street):
        if permit is None:
            raise DiscoveryChoiceRequired("permit", "/SelectPermit", (PermitOption("p1", "Visitor"), PermitOption("p2", "Guest")))
        if street is None:
            raise DiscoveryChoiceRequired("street", "/StepOne", (StreetOption("s1", "Main Street"), StreetOption("s2", "Side Street")))
        return context
    monkeypatch.setattr(module, "discover_context", discover)
    configured = flow(module, FakeHass())
    first = await configured.async_step_user({"email": "a@example.test", "password": "secret"})
    assert first["step_id"] == "permit"
    second = await configured.async_step_permit({"permit": "Visitor"})
    assert second["step_id"] == "street"
    final = await configured.async_step_street({"street": "Main Street"})
    assert final["type"] == "create_entry"
    assert final["data"]["street_label"] == "Main Street"


@pytest.mark.asyncio
async def test_abandoned_choice_flow_discards_its_cookie_session(setup, monkeypatch):
    module, sessions, _ = setup
    async def discover(session, permit, street):
        raise DiscoveryChoiceRequired("permit", "/SelectPermit", (PermitOption("p1", "Visitor"), PermitOption("p2", "Guest")))
    monkeypatch.setattr(module, "discover_context", discover)
    configured = flow(module, FakeHass())
    result = await configured.async_step_user({"email": "a@example.test", "password": "secret"})
    assert result["step_id"] == "permit"
    assert sessions[0].detached is False
    configured.async_remove()
    assert sessions[0].detached is True


@pytest.mark.asyncio
@pytest.mark.parametrize("error,code", [(AuthenticationFailed(), "invalid_auth"), (InteractiveChallenge(), "login_challenge")])
async def test_login_error_stays_on_form(setup, error, code):
    module, sessions, _ = setup
    FakeAuthenticator.error = error
    result = await flow(module, FakeHass()).async_step_user({"email": "a@example.test", "password": "bad"})
    assert result["type"] == "form"
    assert result["errors"]["base"] == code
    assert sessions[0].detached


@pytest.mark.asyncio
async def test_connection_error_stays_on_form_and_discards_session(setup):
    module, sessions, _ = setup
    FakeAuthenticator.error = aiohttp.ClientError()
    result = await flow(module, FakeHass()).async_step_user({"email": "a@example.test", "password": "secret"})
    assert result["errors"]["base"] == "cannot_connect"
    assert sessions[0].detached


@pytest.mark.asyncio
async def test_reauth_changes_only_credentials(setup):
    module, _, _ = setup
    entry = SimpleNamespace(entry_id="one", data={"email": "old@example.test", "password": "old", "permit_label": "Visitor", "street_label": "Main Street"}, options={}, update_listeners=[])
    hass = FakeHass([entry])
    configured = flow(module, hass, "reauth", "one")
    result = await configured.async_step_reauth_confirm({"email": "old@example.test", "password": "new"})
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data == {"email": "old@example.test", "password": "new", "permit_label": "Visitor", "street_label": "Main Street"}
    assert hass.reloads == ["one"]


@pytest.mark.asyncio
async def test_reauth_rejects_different_account_before_login(setup):
    module, sessions, _ = setup
    entry = SimpleNamespace(entry_id="one", title="First", data={"email": "one@example.test", "password": "old", "permit_label": "Visitor", "street_label": "Main Street"}, options={})
    other = SimpleNamespace(entry_id="two", title="Second", data={"email": "two@example.test", "password": "other", "permit_label": "Guest", "street_label": "Side Street"}, options={})
    hass = FakeHass([entry, other])
    result = await flow(module, hass, "reauth", "one").async_step_reauth_confirm({"email": "two@example.test", "password": "new"})
    assert result["errors"]["base"] == "account_mismatch"
    assert entry.data["email"] == "one@example.test"
    assert entry.data["password"] == "old"
    assert hass.reloads == []
    assert sessions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error,code", [(AuthenticationFailed(), "invalid_auth"), (InteractiveChallenge(), "login_challenge")])
async def test_reauth_failure_keeps_existing_binding_and_password(setup, error, code):
    module, sessions, _ = setup
    entry = SimpleNamespace(entry_id="one", title="Paysmarti account", data={"email": "a@example.test", "password": "old", "permit_label": "Visitor", "street_label": "Main Street"}, options={})
    hass = FakeHass([entry])
    FakeAuthenticator.error = error
    result = await flow(module, hass, "reauth", "one").async_step_reauth_confirm({"email": "a@example.test", "password": "bad"})
    assert result["errors"]["base"] == code
    assert entry.data["password"] == "old"
    assert hass.reloads == []
    assert sessions[0].detached


@pytest.mark.asyncio
async def test_runtime_unload_reload_and_save_default(setup, monkeypatch):
    from custom_components.paysmarti_visitor import async_setup_entry, async_unload_entry
    module, _, _ = setup
    sessions = []
    monkeypatch.setattr("custom_components.paysmarti_visitor.async_create_clientsession", lambda hass: sessions.append(Session()) or sessions[-1])
    monkeypatch.setattr("custom_components.paysmarti_visitor.PaysmartiAuthenticator", FakeAuthenticator)
    async def discover(session, permit, street):
        return BookingContext("p1", permit, "s1", street)
    monkeypatch.setattr("custom_components.paysmarti_visitor.discover_context", discover)
    entry = SimpleNamespace(entry_id="one", data={"email": "a@example.test", "password": "secret", "permit_label": "Visitor", "street_label": "Main Street"}, options={})
    hass = FakeHass([entry])
    assert await async_setup_entry(hass, entry)
    assert entry.runtime_data.context.permit_label == "Visitor"
    assert entry.runtime_data.booker is not None
    assert entry.runtime_data.enable_save_new is False
    entry.options["enable_save_new"] = True
    assert entry.runtime_data.enable_save_new is True
    assert await async_unload_entry(hass, entry)
    assert sessions[0].detached
    assert await async_setup_entry(hass, entry)
    assert sessions[1] is entry.runtime_data.session
    assert sessions[1] is not sessions[0]


@pytest.mark.asyncio
async def test_options_enable_save_only_when_chosen(setup):
    module, _, _ = setup
    entry = SimpleNamespace(options={})
    options = module.PaysmartiVisitorOptionsFlow(entry)
    result = await options.async_step_init(None)
    assert result["type"] == "form"
    assert isinstance(result["data_schema"], probatio.Schema)
    result = await options.async_step_init({"enable_save_new": True})
    assert result["data"] == {"enable_save_new": True}
