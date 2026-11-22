"""Minimal async Particle Cloud client."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .const import API


class ParticleAuthError(Exception):
    """Token missing, expired or revoked."""


class ParticleError(Exception):
    """Any other API failure."""


class ParticleClient:
    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, data: dict | None = None) -> Any:
        try:
            async with self._session.request(
                method, f"{API}{path}", headers=self._headers, data=data,
                timeout=aiohttp.ClientTimeout(total=40),
            ) as resp:
                if resp.status == 401:
                    raise ParticleAuthError("Particle rejected the access token")
                body = await resp.json(content_type=None)
                if resp.status >= 400:
                    msg = (body or {}).get("error_description") or (body or {}).get("error") or resp.status
                    raise ParticleError(f"{method} {path}: {msg}")
                return body
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ParticleError(f"{method} {path}: {err}") from err

    async def user(self) -> dict:
        return await self._request("GET", "/user")

    async def devices(self) -> list[dict]:
        return await self._request("GET", "/devices")

    async def device(self, device_id: str) -> dict:
        return await self._request("GET", f"/devices/{device_id}")

    async def variable(self, device_id: str, name: str) -> Any:
        return (await self._request("GET", f"/devices/{device_id}/{name}"))["result"]

    async def call(self, device_id: str, name: str, arg: str = "") -> int:
        res = await self._request("POST", f"/devices/{device_id}/{name}", {"arg": arg})
        return res.get("return_value")

    async def publish(self, name: str, data: str = "", private: bool = True, ttl: int = 60) -> None:
        await self._request("POST", "/devices/events",
                            {"name": name, "data": data, "private": str(private).lower(), "ttl": ttl})

    async def events(self) -> AsyncIterator[tuple[str, dict]]:
        """Yield (event name, payload) from the SSE stream of all the account's devices.

        Keepalives (blank lines, `:ok`) are yielded as ("", {}) so the caller can see
        the socket is alive. Particle sends one about every 9 s, so a 60 s read timeout
        means the connection is dead. A stream can also stay alive yet stop delivering
        events; the caller detects that with a canary event (see probe()).
        """
        async with self._session.get(
            f"{API}/devices/events", headers=self._headers,
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60),
        ) as resp:
            if resp.status == 401:
                raise ParticleAuthError("Particle rejected the access token")
            if resp.status >= 400:
                raise ParticleError(f"event stream: HTTP {resp.status}")
            name = None
            async for raw in resp.content:
                line = raw.decode(errors="replace").rstrip("\r\n")
                if not line or line.startswith(":"):
                    yield "", {}
                elif line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:") and name:
                    try:
                        payload = json.loads(line[5:].strip())
                    except ValueError:
                        payload = {"data": line[5:].strip()}
                    yield name, payload
                    name = None
