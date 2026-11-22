"""Owner-side Particle calls, used only during setup, reconfigure, reauth and revoke.

The flow logs in with username + password (+ MFA), uses that short-lived token to
pick a device, put it in a product and create a minimally scoped API user, then
deletes the login token. Home Assistant only ever stores the API user's token.

Particle doesn't let an API user delete itself or revoke its own token (checked
2026-11-22), so revoking needs an owner login too.
"""
from __future__ import annotations

from typing import Any

import aiohttp

from .api import ParticleAuthError, ParticleError
from .const import API

OAUTH = "https://api.particle.io/oauth/token"
CLIENT = aiohttp.BasicAuth("particle", "particle")
LOGIN_TTL = 3600  # s; the token is deleted at the end of the flow anyway

# Exactly what the integration calls, nothing more.
API_USER_SCOPES = [
    "devices:list",
    "devices:get",
    "devices.variable:get",
    "devices.function:call",
    "events:get",
    "events:send",
]


class InvalidCredentials(ParticleError):
    """Wrong username/password or OTP."""


class MfaRequired(ParticleError):
    def __init__(self, mfa_token: str) -> None:
        super().__init__("MFA required")
        self.mfa_token = mfa_token


async def login(session: aiohttp.ClientSession, username: str, password: str) -> str:
    return await _token(session, {"grant_type": "password", "username": username,
                                   "password": password, "expires_in": str(LOGIN_TTL)})


async def login_otp(session: aiohttp.ClientSession, mfa_token: str, otp: str) -> str:
    return await _token(session, {"grant_type": "urn:custom:mfa-otp", "mfa_token": mfa_token,
                                   "otp": otp.strip().replace(" ", ""), "expires_in": str(LOGIN_TTL)})


async def _token(session: aiohttp.ClientSession, form: dict) -> str:
    try:
        async with session.post(OAUTH, data=form, auth=CLIENT,
                                timeout=aiohttp.ClientTimeout(total=30)) as resp:
            body = await resp.json(content_type=None) or {}
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ParticleError(f"login: {err}") from err
    if resp.status == 200 and body.get("access_token"):
        return body["access_token"]
    if body.get("error") == "mfa_required" and body.get("mfa_token"):
        raise MfaRequired(body["mfa_token"])
    if resp.status in (400, 401, 403):
        raise InvalidCredentials(body.get("error_description") or body.get("error") or "invalid login")
    raise ParticleError(f"login: HTTP {resp.status} {body.get('error_description', '')}")


class ParticleAccount:
    """Calls made with the owner's short-lived login token."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._token = token
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _req(self, method: str, path: str, *, json: Any = None, data: Any = None) -> Any:
        try:
            async with self._session.request(method, f"{API}{path}", headers=self._headers,
                                             json=json, data=data,
                                             timeout=aiohttp.ClientTimeout(total=40)) as resp:
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ParticleError(f"{method} {path}: {err}") from err
        if resp.status == 401:
            raise ParticleAuthError("login expired")
        if resp.status >= 400:
            b = body if isinstance(body, dict) else {}
            raise ParticleError(f"{method} {path}: {b.get('error_description') or b.get('error') or resp.status}")
        return body

    async def logout(self) -> None:
        """Delete the login token. Never raises: it expires within the hour anyway."""
        try:
            await self._req("DELETE", "/access_tokens/current")
        except ParticleError:
            pass

    async def user(self) -> dict:
        return await self._req("GET", "/user")

    async def devices(self) -> list[dict]:
        return await self._req("GET", "/devices")

    async def products(self) -> list[dict]:
        body = await self._req("GET", "/user/products")
        return body.get("products", []) if isinstance(body, dict) else body

    async def create_product(self, name: str, platform_id: int) -> dict:
        body = await self._req("POST", "/user/products", json={"product": {
            "name": name, "description": "Created by Home Assistant (particle-ha)",
            "platform_id": platform_id, "settings": {"location": {"opt_in": False}}}})
        return body.get("product", body) if isinstance(body, dict) else body

    async def has_released_firmware(self, product_id: int) -> bool:
        fw = await self._req("GET", f"/products/{product_id}/firmware")
        return any(f.get("product_default") or f.get("groups") for f in fw or [])

    async def import_device(self, product_id: int, device_id: str) -> None:
        res = await self._req("POST", f"/products/{product_id}/devices", data={"id": device_id})
        if device_id in (res.get("invalidDeviceIds") or []) + (res.get("nonmemberDeviceIds") or []):
            raise ParticleError(f"Particle refused to add {device_id} to product {product_id}")

    async def set_development(self, product_id: int, device_id: str) -> None:
        await self._req("PUT", f"/products/{product_id}/devices/{device_id}", data={"development": "true"})

    async def create_api_user(self, product_id: int, friendly_name: str) -> tuple[str, str]:
        res = await self._req("POST", f"/products/{product_id}/team",
                              json={"friendly_name": friendly_name, "scopes": API_USER_SCOPES})
        created = res["created"]
        return created["username"], created["tokens"][0]["token"]

    async def delete_api_user(self, product_id: int, username: str) -> bool:
        """True if deleted, False if it was already gone."""
        try:
            await self._req("DELETE", f"/products/{product_id}/team/{username}")
        except ParticleError as err:
            if "404" in str(err) or "not found" in str(err).lower():
                return False
            raise
        return True
