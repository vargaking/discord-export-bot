"""A small Discord REST client: bot auth, rate limits, downloads."""
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx

API_BASE = "https://discord.com/api/v10"
MAX_RATE_LIMIT_RETRIES = 10
MAX_SERVER_ERROR_RETRIES = 3
MISSING_ACCESS = 50001


class DiscordError(Exception):
    """A failed request. The message never contains the token."""

    def __init__(self, status: int, what: str, code: int | None = None):
        super().__init__(f"Discord answered {status} for {what}")
        self.status = status
        self.code = code


class Forbidden(DiscordError):
    """The bot isn't allowed to see this."""


class DiscordClient:
    def __init__(
        self,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str = API_BASE,
    ):
        self._sleep = sleep
        self._base_url = base_url
        self._pause = 0.0
        self._api = httpx.Client(
            transport=transport,
            headers={"Authorization": f"Bot {token}"},
            timeout=30,
        )
        # The CDN gets no Authorization header.
        self._cdn = httpx.Client(transport=transport, timeout=60, follow_redirects=True)

    def close(self) -> None:
        self._api.close()
        self._cdn.close()

    def get(self, path: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        response = self._send(self._api, self._base_url + path, params, path)
        return response.json()

    def download(self, url: str, dest: Path) -> None:
        """Write the file at url to dest, through dest.part."""
        part = dest.with_name(dest.name + ".part")
        what = httpx.URL(url).path
        response = self._send(self._cdn, url, None, what, stream=True)
        try:
            with open(part, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
        finally:
            response.close()
        os.replace(part, dest)

    def _send(self, client, url, params, what, stream=False) -> httpx.Response:
        rate_limited = 0
        server_errors = 0
        while True:
            if self._pause > 0:
                self._sleep(self._pause)
                self._pause = 0.0
            request = client.build_request("GET", url, params=params)
            response = client.send(request, stream=stream)
            if response.status_code == 429:
                self._close(response, stream)
                rate_limited += 1
                if rate_limited > MAX_RATE_LIMIT_RETRIES:
                    raise DiscordError(429, what)
                self._sleep(_retry_after(response))
                continue
            if response.status_code >= 500 and server_errors < MAX_SERVER_ERROR_RETRIES:
                self._close(response, stream)
                server_errors += 1
                self._sleep(server_errors)
                continue
            self._note_bucket(response)
            if response.is_success:
                return response
            code = _error_code(response) if not stream else None
            self._close(response, stream)
            error = Forbidden if response.status_code == 403 else DiscordError
            raise error(response.status_code, what, code)

    def _note_bucket(self, response: httpx.Response) -> None:
        if response.headers.get("X-RateLimit-Remaining") == "0":
            self._pause = _number(response.headers.get("X-RateLimit-Reset-After"))

    @staticmethod
    def _close(response: httpx.Response, stream: bool) -> None:
        if stream:
            response.close()
        else:
            response.read()


def _number(value) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _retry_after(response: httpx.Response) -> float:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and "retry_after" in body:
        return _number(body["retry_after"])
    return _number(response.headers.get("Retry-After")) or 1.0


def _error_code(response: httpx.Response) -> int | None:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, int) else None
