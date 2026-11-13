"""Account login, permit/street choice, reauthentication and options."""

import probatio as vol
import aiohttp

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .auth import PaysmartiAuthenticator
from .const import ALLOWED_HOSTS, DOMAIN
from .discovery import discover_context
from .models import AuthenticationFailed, DiscoveryChoiceRequired, InteractiveChallenge, UnexpectedPage, UnexpectedRedirect

_ACCOUNT = vol.Schema({
    vol.Required("email"): str,
    vol.Required("password"): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
})


class PaysmartiVisitorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a Paysmarti account from fresh, validated site data."""

    VERSION = 1

    def __init__(self) -> None:
        self._session = None
        self._credentials = None
        self._permit = None
        self._street = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PaysmartiVisitorOptionsFlow(config_entry)

    async def _close(self):
        if self._session is not None:
            self._session.detach()
            self._session = None

    async def _discover(self):
        try:
            context = await discover_context(self._session, self._permit, self._street)
        except DiscoveryChoiceRequired as exc:
            if exc.stage == "permit":
                self._permit_choices = {option.label for option in exc.options}
                return self.async_show_form(step_id="permit", data_schema=vol.Schema({vol.Required("permit"): vol.In(sorted(self._permit_choices))}))
            self._street_choices = {option.label for option in exc.options}
            return self.async_show_form(step_id="street", data_schema=vol.Schema({vol.Required("street"): vol.In(sorted(self._street_choices))}))
        except (UnexpectedPage, UnexpectedRedirect, aiohttp.ClientError, TimeoutError, OSError):
            await self._close()
            return self.async_abort(reason="booking_context_changed")
        await self._close()
        email, password = self._credentials
        return self.async_create_entry(
            title=email,
            data={"email": email, "password": password,
                  "permit_label": context.permit_label, "street_label": context.street_label},
        )

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=_ACCOUNT)
        email = user_input["email"].strip().lower()
        if any(entry.data.get("email", "").lower() == email for entry in self.hass.config_entries.async_entries(DOMAIN)):
            return self.async_show_form(step_id="user", data_schema=_ACCOUNT, errors={"base": "already_configured"})
        self.context["unique_id"] = email
        self._credentials = (email, user_input["password"])
        self._session = async_create_clientsession(self.hass)
        try:
            await PaysmartiAuthenticator(self._session, ALLOWED_HOSTS).login(*self._credentials)
        except AuthenticationFailed:
            await self._close()
            return self.async_show_form(step_id="user", data_schema=_ACCOUNT, errors={"base": "invalid_auth"})
        except InteractiveChallenge:
            await self._close()
            return self.async_show_form(step_id="user", data_schema=_ACCOUNT, errors={"base": "login_challenge"})
        except (UnexpectedRedirect, aiohttp.ClientError, TimeoutError, OSError):
            await self._close()
            return self.async_show_form(step_id="user", data_schema=_ACCOUNT, errors={"base": "cannot_connect"})
        return await self._discover()

    async def async_step_permit(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="permit", data_schema=vol.Schema({vol.Required("permit"): vol.In(sorted(self._permit_choices))}))
        if user_input["permit"] not in self._permit_choices:
            return await self.async_step_permit()
        self._permit = user_input["permit"]
        return await self._discover()

    async def async_step_street(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="street", data_schema=vol.Schema({vol.Required("street"): vol.In(sorted(self._street_choices))}))
        if user_input["street"] not in self._street_choices:
            return await self.async_step_street()
        self._street = user_input["street"]
        return await self._discover()

    async def async_step_reauth(self, entry_data):
        return self.async_show_form(step_id="reauth_confirm", data_schema=_ACCOUNT)

    async def async_step_reauth_confirm(self, user_input=None):
        if user_input is None:
            return await self.async_step_reauth(None)
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="account_missing")
        session = async_create_clientsession(self.hass)
        try:
            await PaysmartiAuthenticator(session, ALLOWED_HOSTS).login(user_input["email"], user_input["password"])
            await discover_context(session, entry.data["permit_label"], entry.data["street_label"])
        except AuthenticationFailed:
            return self.async_show_form(step_id="reauth_confirm", data_schema=_ACCOUNT, errors={"base": "invalid_auth"})
        except InteractiveChallenge:
            return self.async_show_form(step_id="reauth_confirm", data_schema=_ACCOUNT, errors={"base": "login_challenge"})
        except (UnexpectedPage, UnexpectedRedirect, aiohttp.ClientError, TimeoutError, OSError):
            return self.async_show_form(step_id="reauth_confirm", data_schema=_ACCOUNT, errors={"base": "cannot_connect"})
        finally:
            session.detach()
        return self.async_update_reload_and_abort(
            entry, unique_id=user_input["email"].strip().lower(),
            data_updates={"email": user_input["email"].strip().lower(), "password": user_input["password"]}
        )


class PaysmartiVisitorOptionsFlow(config_entries.OptionsFlow):
    """Opt into the site's Save new vehicle behavior explicitly."""

    def __init__(self, entry):
        self._entry = entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data={"enable_save_new": user_input.get("enable_save_new", False) is True})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Optional("enable_save_new", default=self._entry.options.get("enable_save_new", False)): bool}),
        )
