"""Authentication flow tests with queued, sanitized HTTP responses."""

import asyncio
from collections import deque
from functools import wraps
from pathlib import Path

import pytest

from custom_components.paysmarti_visitor.auth import PaysmartiAuthenticator
from custom_components.paysmarti_visitor.const import ALLOWED_HOSTS
from custom_components.paysmarti_visitor.models import (
    AuthenticationFailed,
    InteractiveChallenge,
    UnexpectedRedirect,
)


LOGIN = "https://permits.paysmarti.co.uk/login/LeedsCC/Home"
VISITOR = "https://visitor.paysmarti.co.uk/LeedsCC/BookParking/SelectPermit"
ACCOUNT = "https://permits.paysmarti.co.uk/session"
IDP = "https://permits.paysmarti.co.uk/sso"
HOSTS = ALLOWED_HOSTS
PERMIT_HTML = (Path(__file__).parent / "fixtures" / "permit.html").read_text()


def async_test(test):
    @wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run

LOGIN_HTML = '''<form action="/login/LeedsCC/Home" method="post">
<input name="__RequestVerificationToken" value="fresh-token">
<input name="returnUrl" value="/home"><input name="email"><input name="password" type="password">
</form>'''
FEDERATION = '''<form method="post" action="https://permits.paysmarti.co.uk/session">
<input name="wa" value="wsignin1.0"><input name="wresult" value="sanitized-ticket">
<input name="wctx" value="sanitized-context"></form>'''
VISITOR_FEDERATION = '''<form method="post" action="https://visitor.paysmarti.co.uk/LeedsCC/signin">
<input name="wa" value="wsignin1.0"><input name="wresult" value="fresh-visitor-ticket">
<input name="wctx" value="visitor-context"></form>'''


class Response:
    def __init__(self, status=200, body="", location=None, set_cookie=None):
        self.status = status
        self.body = body
        self.headers = {"Location": location} if location else {}
        self.set_cookie = set_cookie

    async def text(self):
        return self.body


class Request:
    def __init__(self, transport, method, url, data, allow_redirects, timeout):
        self.transport = transport
        self.method = method
        self.url = url
        self.data = data
        self.allow_redirects = allow_redirects
        self.timeout = timeout

    async def __aenter__(self):
        assert self.transport.queue, f"unexpected {self.method} {self.url}"
        expected_method, expected_url, response = self.transport.queue.popleft()
        assert (self.method, self.url) == (expected_method, expected_url)
        assert self.allow_redirects is False
        assert 0 < self.timeout <= 30
        if self.method == "GET" and self.url == VISITOR and response.body == PERMIT_HTML:
            assert self.transport.cookies.get("visitor-session") == "fresh-cookie"
        if response.set_cookie:
            self.transport.cookies[response.set_cookie[0]] = response.set_cookie[1]
        self.transport.calls.append((self.method, self.url, self.data))
        return response

    async def __aexit__(self, *_):
        return False


class Session:
    def __init__(self, *steps):
        self.queue = deque(steps)
        self.calls = []
        self.cookies = {}

    def request(self, method, url, *, data=None, allow_redirects=True, timeout=None):
        return Request(self, method, url, data, allow_redirects, timeout)


def step(method, url, status=200, body="", location=None, set_cookie=None):
    return method, url, Response(status, body, location, set_cookie)


def successful_login_steps():
    return [
        step("GET", LOGIN, body=LOGIN_HTML),
        step("POST", LOGIN, status=302, location=IDP),
        step("GET", IDP, body=FEDERATION),
        step("POST", ACCOUNT, status=302, location="/home"),
        step("GET", "https://permits.paysmarti.co.uk/home", body="Account home"),
        step("GET", VISITOR, status=302, location=IDP),
        step("GET", IDP, body=VISITOR_FEDERATION),
        step("POST", "https://visitor.paysmarti.co.uk/LeedsCC/signin", status=302, location="/LeedsCC/BookParking/SelectPermit", set_cookie=("visitor-session", "fresh-cookie")),
        step("GET", VISITOR, body=PERMIT_HTML),
    ]


@async_test
async def test_login_posts_fresh_form_and_completes_account_and_visitor_handoffs():
    session = Session(*successful_login_steps())

    await PaysmartiAuthenticator(session, HOSTS).login("user@example.test", "private-password")

    assert not session.queue
    assert session.calls[1][2] == {
        "__RequestVerificationToken": "fresh-token", "returnUrl": "/home",
        "email": "user@example.test", "password": "private-password",
    }
    assert session.calls[3][2]["wresult"] == "sanitized-ticket"
    assert session.calls[7][2]["wresult"] == "fresh-visitor-ticket"
    assert session.cookies["visitor-session"] == "fresh-cookie"


@async_test
async def test_visitor_200_error_page_is_not_an_authenticated_session():
    steps = successful_login_steps()
    steps[-1] = step("GET", VISITOR, body="<h1>Service unavailable</h1>")
    session = Session(*steps)

    with pytest.raises(AuthenticationFailed):
        await PaysmartiAuthenticator(session, HOSTS).login("user@example.test", "secret")

    assert not session.queue
    assert all("StepOne" not in url and "StepTwo" not in url for _, url, _ in session.calls)


@async_test
async def test_visitor_probe_renews_expired_session_before_booking():
    session = Session(*successful_login_steps())
    auth = PaysmartiAuthenticator(session, HOSTS)
    await auth.login("user@example.test", "private-password")
    session.queue.extend([
        step("GET", VISITOR, status=302, location=LOGIN + "?returnUrl=%2FLeedsCC"),
        *successful_login_steps(),
    ])

    await auth.ensure_visitor_session()

    assert not session.queue
    assert all("StepOne" not in url and "StepTwo" not in url for _, url, _ in session.calls)


@async_test
async def test_wrong_password_stops_before_visitor_request():
    session = Session(step("GET", LOGIN, body=LOGIN_HTML), step("POST", LOGIN, body=LOGIN_HTML))

    with pytest.raises(AuthenticationFailed):
        await PaysmartiAuthenticator(session, HOSTS).login("user@example.test", "wrong")

    assert [url for _, url, _ in session.calls] == [LOGIN, LOGIN]


@pytest.mark.parametrize("challenge", ["CAPTCHA", "multi-factor authentication", "verification code"])
@async_test
async def test_interactive_challenge_stops_before_booking(challenge):
    session = Session(step("GET", LOGIN, body=LOGIN_HTML), step("POST", LOGIN, body=f"<h1>{challenge}</h1>"))

    with pytest.raises(InteractiveChallenge):
        await PaysmartiAuthenticator(session, HOSTS).login("user@example.test", "secret")

    assert [url for _, url, _ in session.calls] == [LOGIN, LOGIN]


@pytest.mark.parametrize("location", ["https://evil.test/steal", "http://permits.paysmarti.co.uk/session", "//evil.test/steal"])
@async_test
async def test_rejects_unrecognized_or_non_https_redirect_without_following(location):
    session = Session(step("GET", LOGIN, body=LOGIN_HTML), step("POST", LOGIN, status=302, location=location))

    with pytest.raises(UnexpectedRedirect):
        await PaysmartiAuthenticator(session, HOSTS).login("user@example.test", "secret")

    assert [url for _, url, _ in session.calls] == [LOGIN, LOGIN]


@async_test
async def test_rejects_unrecognized_federation_form_target():
    bad_form = FEDERATION.replace("permits.paysmarti.co.uk", "evil.test")
    session = Session(step("GET", LOGIN, body=LOGIN_HTML), step("POST", LOGIN, status=302, location=IDP), step("GET", IDP, body=bad_form))

    with pytest.raises(UnexpectedRedirect):
        await PaysmartiAuthenticator(session, HOSTS).login("user@example.test", "secret")

    assert len(session.calls) == 3
