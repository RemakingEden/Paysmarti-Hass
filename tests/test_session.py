"""Paysmarti's federation cookie must be sent in browser-compatible form."""

from http.cookies import SimpleCookie

import pytest
from yarl import URL


@pytest.mark.asyncio
async def test_session_sends_federation_cookie_without_quotes(monkeypatch):
    from custom_components.paysmarti_visitor import session as session_module

    captured = {}
    monkeypatch.setattr(session_module, "async_create_clientsession", lambda hass, **kwargs: captured.update(kwargs))
    session_module.create_paysmarti_session(object())

    cookie = SimpleCookie()
    cookie["FedAuth"] = "abc+def/ghi=="
    cookie["FedAuth"]["path"] = "/login/LeedsCC/"
    jar = captured["cookie_jar"]
    jar.update_cookies(cookie, response_url=URL("https://permits.paysmarti.co.uk/login/LeedsCC/Home"))
    sent = jar.filter_cookies(URL("https://permits.paysmarti.co.uk/login/LeedsCC/"))
    assert sent.output(header="", sep="").strip() == "FedAuth=abc+def/ghi=="
