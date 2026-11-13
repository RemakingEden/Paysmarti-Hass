"""Bounded Paysmarti login and visitor-site federation handoff."""

import logging
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from .const import LOGIN_HOST, VISITOR_HOST
from .forms import permit_forms
from .models import AuthenticationFailed, InteractiveChallenge, UnexpectedPage, UnexpectedRedirect

LOGIN_URL = f"https://{LOGIN_HOST}/login/LeedsCC/Home"
LOGIN_PAGE_PATH = "/login/LeedsCC/"
VISITOR_URL = f"https://{VISITOR_HOST}/LeedsCC/BookParking/SelectPermit"
_MAX_HOPS = 12
_CHALLENGE_MARKERS = ("captcha", "multi-factor", "multifactor", "verification code", "one-time code", "two-factor")
_LOGGER = logging.getLogger(__name__)
_SAFE_ROUTES = {
    (LOGIN_HOST, "/login/LeedsCC/Home"): "credential submit",
    (LOGIN_HOST, LOGIN_PAGE_PATH): "identity handoff",
    (LOGIN_HOST, "/acct/LeedsCC/"): "account session",
    (VISITOR_HOST, "/LeedsCC"): "visitor handoff",
    (VISITOR_HOST, "/LeedsCC/"): "visitor entry",
    (VISITOR_HOST, "/leedscc/"): "visitor entry",
    (VISITOR_HOST, "/LeedsCC/BookParking/SelectPermit"): "visitor permit",
}


class _ExpiredSession(Exception):
    pass


class PaysmartiAuthenticator:
    """Authenticate through only explicitly allowed HTTPS hosts.

    The supplied session owns cookies; this object retains credentials only to
    renew a visitor session before a booking starts.
    """

    def __init__(self, session, allowed_hosts) -> None:
        self._session = session
        self._allowed_hosts = frozenset(allowed_hosts)
        self._credentials: tuple[str, str] | None = None

    def _trusted(self, base: str, target: str) -> str:
        url = urljoin(base, target)
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in self._allowed_hosts or parts.username or parts.password or parts.port not in (None, 443):
            raise UnexpectedRedirect("Authentication target is outside the allowed HTTPS hosts")
        return url

    async def _request(self, method: str, url: str, data: dict[str, str] | None = None, *, headers: dict[str, str] | None = None):
        url = self._trusted(url, url)
        async with self._session.request(method, url, data=data, headers=headers, allow_redirects=False, timeout=30) as response:
            if response.status != 200 and not 300 <= response.status < 400:
                parts = urlsplit(url)
                route = _SAFE_ROUTES.get((parts.hostname, parts.path), "other trusted route")
                _LOGGER.warning("Paysmarti authentication %s %s returned HTTP %s", method, route, response.status)
            return response.status, await response.text(), response.headers.get("Location")

    @staticmethod
    def _challenge(html: str) -> None:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
        if any(marker in text for marker in _CHALLENGE_MARKERS):
            raise InteractiveChallenge("Login requires an interactive challenge")

    @staticmethod
    def _federation_form(html: str):
        soup = BeautifulSoup(html, "html.parser")
        matches = []
        for form in soup.find_all("form"):
            fields = {str(field.get("name")): str(field.get("value", "")) for field in form.select("input[name]")}
            if {"wa", "wresult", "wctx"}.issubset(fields) and fields["wa"] == "wsignin1.0":
                matches.append((form, fields))
        if len(matches) > 1:
            raise AuthenticationFailed("Ambiguous federation handoff")
        return matches[0] if matches else None

    async def _advance(self, url: str, status: int, html: str, location: str | None, *, visitor: bool) -> None:
        for _ in range(_MAX_HOPS):
            if 300 <= status < 400:
                if not location:
                    raise AuthenticationFailed("Authentication redirect has no target")
                target = self._trusted(url, location)
                target_parts = urlsplit(target)
                if visitor and target_parts.hostname == LOGIN_HOST and target_parts.path == urlsplit(LOGIN_URL).path:
                    raise _ExpiredSession
                url = target
                status, html, location = await self._request("GET", url)
                continue
            if status != 200:
                raise AuthenticationFailed("Authentication request failed")
            self._challenge(html)
            federation = self._federation_form(html)
            if federation:
                form, fields = federation
                if str(form.get("method", "get")).lower() != "post":
                    raise AuthenticationFailed("Unexpected federation method")
                target = self._trusted(url, str(form.get("action", "")))
                url = target
                status, html, location = await self._request("POST", url, fields)
                continue
            if visitor and urlsplit(url).hostname == LOGIN_HOST and urlsplit(url).path == LOGIN_PAGE_PATH:
                raise _ExpiredSession
            if urlsplit(url).path == urlsplit(LOGIN_URL).path and urlsplit(url).hostname == LOGIN_HOST:
                raise AuthenticationFailed("Login did not establish a session")
            if visitor and (urlsplit(url).hostname != VISITOR_HOST or urlsplit(url).path != urlsplit(VISITOR_URL).path):
                raise AuthenticationFailed("Visitor session was not established")
            if visitor:
                try:
                    choices = permit_forms(html, "/LeedsCC/BookParking/StepOne")
                    if not choices:
                        raise UnexpectedPage("permit", "/LeedsCC/BookParking/StepOne")
                except UnexpectedPage as exc:
                    raise AuthenticationFailed("Visitor permit form unavailable") from exc
                for _, permit_form in choices:
                    self._trusted(url, permit_form.action)
            return
        raise AuthenticationFailed("Authentication handoff exceeded redirect limit")

    async def _login_account(self, email: str, password: str) -> None:
        # The browser reaches the login page through the visitor site's
        # federation redirect. /login/LeedsCC/Home is its POST endpoint; a
        # direct GET there returns a server error and is not a login form.
        status, _, location = await self._request("GET", VISITOR_URL)
        if not 300 <= status < 400 or not location:
            raise AuthenticationFailed("Login handoff unavailable")
        login_page = self._trusted(VISITOR_URL, location)
        parts = urlsplit(login_page)
        query = parse_qs(parts.query)
        if parts.hostname != LOGIN_HOST or parts.path != LOGIN_PAGE_PATH or query.get("wa") != ["wsignin1.0"] or not all(query.get(key) for key in ("wctx", "wreply", "wtrealm")):
            raise UnexpectedRedirect("Unexpected login handoff")
        status, html, _ = await self._request("GET", login_page)
        if status != 200:
            raise AuthenticationFailed("Login page unavailable")
        self._challenge(html)
        fields = {"email": email, "password": password, "returnUrl": ""}
        status, html, location = await self._request("POST", LOGIN_URL, fields,
            headers={"Origin": f"https://{LOGIN_HOST}", "Referer": login_page})
        await self._advance(LOGIN_URL, status, html, location, visitor=False)

    async def _visitor(self) -> None:
        status, html, location = await self._request("GET", VISITOR_URL)
        await self._advance(VISITOR_URL, status, html, location, visitor=True)

    async def login(self, email: str, password: str) -> None:
        """Establish account and visitor cookies using the supplied session."""
        await self._login_account(email, password)
        try:
            await self._visitor()
        except _ExpiredSession as exc:
            raise AuthenticationFailed("Visitor handoff returned to login") from exc
        self._credentials = (email, password)

    async def ensure_visitor_session(self) -> None:
        """Probe visitor access, renewing expired login once before booking."""
        try:
            await self._visitor()
        except _ExpiredSession:
            if self._credentials is None:
                raise AuthenticationFailed("Login required") from None
            await self.login(*self._credentials)
