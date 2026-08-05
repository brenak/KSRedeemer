"""Async HTTP client for the kingshot_web API.

Ported from KSCompanion's ks_client.py so both bots authenticate the same
way, against the same account. Handles Django session authentication
transparently: on the first request (or after a session expires) it POSTs
credentials to /accounts/login/ and caches the resulting session cookie for
all subsequent calls.
"""

import aiohttp
from config.config import KINGSHOT_URL, KINGSHOT_USERNAME, KINGSHOT_PASSWORD


class KingshotAPIError(Exception):
    pass


class KingshotClient:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._authenticated = False

    def configured(self) -> bool:
        return bool(KINGSHOT_URL and KINGSHOT_USERNAME and KINGSHOT_PASSWORD)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # unsafe=True is required for localhost/IP-based URLs
            jar = aiohttp.CookieJar(unsafe=True)
            self._session = aiohttp.ClientSession(cookie_jar=jar)
            self._authenticated = False
        return self._session

    async def _login(self) -> None:
        session = await self._get_session()
        login_url = f"{KINGSHOT_URL}/accounts/login/"

        async with session.get(login_url) as resp:
            if resp.status != 200:
                raise KingshotAPIError(f"Login page returned HTTP {resp.status}")

        cookies = session.cookie_jar.filter_cookies(KINGSHOT_URL)
        csrf_morsel = cookies.get("csrftoken")
        if not csrf_morsel:
            raise KingshotAPIError("No CSRF token on login page — is KINGSHOT_URL correct?")

        async with session.post(
            login_url,
            data={
                "username": KINGSHOT_USERNAME,
                "password": KINGSHOT_PASSWORD,
                "csrfmiddlewaretoken": csrf_morsel.value,
                "next": "/",
            },
            headers={"Referer": login_url},
            allow_redirects=False,
        ) as resp:
            if resp.status not in (301, 302):
                raise KingshotAPIError(
                    "Login failed — check KINGSHOT_USERNAME / KINGSHOT_PASSWORD. "
                    f"(server returned HTTP {resp.status})"
                )

        cookies = session.cookie_jar.filter_cookies(KINGSHOT_URL)
        if "sessionid" not in cookies:
            raise KingshotAPIError("Login did not produce a session cookie — credentials rejected?")

        self._authenticated = True

    async def _get_json(self, path: str, **params: object) -> dict:
        if not self.configured():
            raise KingshotAPIError("KINGSHOT_URL / KINGSHOT_USERNAME / KINGSHOT_PASSWORD not configured")

        session = await self._get_session()
        if not self._authenticated:
            await self._login()

        url = f"{KINGSHOT_URL}{path}"
        query = {k: v for k, v in params.items() if v is not None}

        async with session.get(url, params=query, allow_redirects=False) as resp:
            if resp.status in (301, 302):
                # Session expired — re-authenticate and retry once
                self._authenticated = False
                await self._login()
                async with session.get(url, params=query) as resp2:
                    if not resp2.ok:
                        raise KingshotAPIError(f"API error after re-auth: HTTP {resp2.status}")
                    return await resp2.json()

            if not resp.ok:
                raise KingshotAPIError(f"API error: HTTP {resp.status}")

            return await resp.json()

    async def search_cities(self, search: str, kid: str | None = None) -> dict:
        return await self._get_json("/api/cities/", search=search, kid=kid)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
