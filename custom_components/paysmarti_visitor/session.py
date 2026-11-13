"""Create an isolated HTTP session with Paysmarti-compatible cookies."""

import aiohttp
from homeassistant.helpers.aiohttp_client import async_create_clientsession


def create_paysmarti_session(hass) -> aiohttp.ClientSession:
    """Preserve the unquoted FedAuth cookie format sent by browsers."""
    return async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar(quote_cookie=False))
