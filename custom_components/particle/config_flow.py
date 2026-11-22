"""Setup: log in to Particle, pick a device, put it in a product, create a scoped API user.

Home Assistant keeps only the API user's token (product scope, six permissions).
The owner login is short-lived and deleted at the end of every flow that used it.

Flows:
- user / reconfigure: login (+ MFA) -> device -> product (existing or new; the device is
  imported if needed) -> API user. Reconfigure switches an entry in place, so entity
  IDs, HomeKit and history are kept; its old API user is deleted.
- reauth: login -> new API user for the entry's product (old one deleted).
- options: Devices (pick more devices of the product) or Revoke (login -> delete the
  API user; the entry then waits for re-authentication).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (SOURCE_RECONFIGURE, SOURCE_REAUTH, ConfigEntry,
                                          ConfigFlow, ConfigFlowResult, OptionsFlow)
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (NumberSelector, NumberSelectorConfig, SelectOptionDict,
                                            SelectSelector, SelectSelectorConfig,
                                            SelectSelectorMode, TextSelector, TextSelectorConfig,
                                            TextSelectorType)

from .account import InvalidCredentials, MfaRequired, ParticleAccount, login, login_otp
from .api import ParticleAuthError, ParticleClient, ParticleError
from .const import (CONF_API_USER, CONF_DEVICES, CONF_POLL_INTERVAL, CONF_PRODUCT_ID,
                    CONF_PRODUCT_NAME, CONF_PRODUCT_SLUG, DEFAULT_POLL_INTERVAL, DOMAIN,
                    PLATFORM_NAMES)

NEW_PRODUCT = "__new__"
KEEP_PRODUCT = "__keep__"

LOGIN_SCHEMA = vol.Schema({
    vol.Required(CONF_USERNAME): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL,
                                                                 autocomplete="username")),
    vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD,
                                                                 autocomplete="current-password")),
})
MFA_SCHEMA = vol.Schema({vol.Required("otp"): TextSelector(TextSelectorConfig(autocomplete="one-time-code"))})


def platform_name(pid: int | None) -> str:
    return PLATFORM_NAMES.get(pid, f"platform {pid}")


def is_product_device(d: dict) -> bool:
    """Sandbox devices report product_id == platform_id."""
    return bool(d.get("product_id")) and d.get("product_id") != d.get("platform_id")


def api_user_name(hass) -> str:
    return f"Home Assistant ({hass.config.location_name or 'home'})"[:60]


class LoginSteps:
    """login + mfa steps, shared by the config and options flows."""

    _account: ParticleAccount | None = None
    _mfa_token: str | None = None

    async def async_step_login(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                token = await login(session, user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except MfaRequired as err:
                self._mfa_token = err.mfa_token
                return await self.async_step_mfa()
            except InvalidCredentials:
                errors["base"] = "invalid_auth"
            except ParticleError:
                errors["base"] = "cannot_connect"
            else:
                self._account = ParticleAccount(session, token)
                return await self._logged_in()
        return self.async_show_form(step_id="login", data_schema=LOGIN_SCHEMA, errors=errors,
                                    description_placeholders=self._login_placeholders())

    async def async_step_mfa(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                token = await login_otp(session, self._mfa_token or "", user_input["otp"])
            except InvalidCredentials:
                errors["base"] = "invalid_otp"
            except ParticleError:
                errors["base"] = "cannot_connect"
            else:
                self._account = ParticleAccount(session, token)
                return await self._logged_in()
        return self.async_show_form(step_id="mfa", data_schema=MFA_SCHEMA, errors=errors)

    def _login_placeholders(self) -> dict[str, str]:
        return {"why": ""}

    async def _logged_in(self) -> ConfigFlowResult:
        raise NotImplementedError

    async def _done(self) -> None:
        """Delete the login token."""
        if self._account:
            await self._account.logout()
            self._account = None

    @callback
    def async_remove(self) -> None:
        """Flow cancelled or abandoned: delete the login token now, not in an hour."""
        if self._account:
            account, self._account = self._account, None
            self.hass.async_create_background_task(account.logout(), "particle logout")


class ParticleConfigFlow(LoginSteps, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._devices: list[dict] = []
        self._products: list[dict] = []
        self._device: dict | None = None
        self._product: dict | None = None
        self._current: dict | None = None  # product the device is in now, if any

    # --- entry points ------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_login()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_login()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_login()

    def _login_placeholders(self) -> dict[str, str]:
        if self.source == SOURCE_REAUTH:
            return {"why": "The API user's token stopped working. Log in to create a new one."}
        if self.source == SOURCE_RECONFIGURE:
            return {"why": "Log in to choose the device and product again; a new API user replaces the old one."}
        return {"why": ""}

    async def _logged_in(self) -> ConfigFlowResult:
        try:
            self._devices = await self._account.devices()
            self._products = await self._account.products()
        except ParticleError:
            await self._done()
            return self.async_abort(reason="cannot_connect")
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            pid = entry.data.get(CONF_PRODUCT_ID)
            if pid:
                self._product = next((p for p in self._products if p["id"] == pid),
                                     {"id": pid, "name": entry.data.get(CONF_PRODUCT_NAME, str(pid)),
                                      "slug": entry.data.get(CONF_PRODUCT_SLUG)})
                return await self._finish()
            # legacy account-token entry: choose a device like reconfigure
        return await self.async_step_device()

    # --- device and product ----------------------------------------------------------

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._device = next(d for d in self._devices if d["id"] == user_input["device"])
            if is_product_device(self._device):
                pid = self._device["product_id"]
                self._current = next((p for p in self._products if p["id"] == pid), None)
                if self._current is None:
                    return self.async_show_form(step_id="device", data_schema=self._device_schema(),
                                                errors={"base": "not_product_admin"})
            else:
                self._current = None
            return await self.async_step_product()
        return self.async_show_form(step_id="device", data_schema=self._device_schema(),
                                    description_placeholders={"count": str(len(self._devices))})

    def _device_schema(self) -> vol.Schema:
        names = {p["id"]: p["name"] for p in self._products}
        opts = []
        for d in sorted(self._devices, key=lambda d: (not d.get("online"), (d.get("name") or "").lower())):
            where = (names.get(d.get("product_id"), f"product {d.get('product_id')}")
                     if is_product_device(d) else "sandbox")
            state = "online" if d.get("online") else "offline"
            opts.append(SelectOptionDict(value=d["id"], label=(
                f"{d.get('name') or d['id']} · {platform_name(d.get('platform_id'))} · {where} · {state}")))
        return vol.Schema({vol.Required("device"): SelectSelector(
            SelectSelectorConfig(options=opts, mode=SelectSelectorMode.DROPDOWN))})

    async def async_step_product(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        dev = self._device
        platform = dev["platform_id"]
        current = self._current
        same = [p for p in self._products if p.get("platform_id") == platform
                and not (current and p["id"] == current["id"])]
        errors: dict[str, str] = {}
        if user_input is not None:
            choice = user_input["product"]
            if current and choice == KEEP_PRODUCT:
                self._product = current
                return await self._finish()
            try:
                if choice == NEW_PRODUCT:
                    name = (user_input.get("name") or "").strip()
                    if not name:
                        errors["name"] = "name_required"
                        raise _Retry
                    self._product = await self._account.create_product(name, platform)
                    self._products.append(self._product)
                else:
                    self._product = next(p for p in same if str(p["id"]) == choice)
                pid = self._product["id"]
                if current:  # move: a device is in one product at a time
                    await self._account.remove_from_product(current["id"], dev["id"])
                    current = self._current = None
                await self._account.import_device(pid, dev["id"])
                # Released product firmware would replace the device's own app.
                if await self._account.has_released_firmware(pid):
                    await self._account.set_development(pid, dev["id"])
            except _Retry:
                pass
            except ParticleError as err:
                errors["base"] = "product_failed"
                self._error = str(err)
            else:
                return await self._finish()
        opts = []
        if current:
            opts.append(SelectOptionDict(value=KEEP_PRODUCT,
                                         label=f"Keep it in {current['name']} ({current['id']})"))
        opts += [SelectOptionDict(value=str(p["id"]),
                                  label=(f"Move it to {p['name']} ({p['id']})" if current
                                         else f"{p['name']} ({p['id']})")) for p in same]
        opts.append(SelectOptionDict(value=NEW_PRODUCT,
                                     label="Move it to a new product…" if current else "New product…"))
        schema = vol.Schema({
            vol.Required("product", default=opts[0]["value"]): SelectSelector(
                SelectSelectorConfig(options=opts, mode=SelectSelectorMode.LIST)),
            vol.Optional("name", default=f"Home Assistant {platform_name(platform)}"): str,
        })
        return self.async_show_form(step_id="product", data_schema=schema, errors=errors,
                                    description_placeholders={
                                        "current": (f"It is in **{current['name']}** now." if current
                                                    else "It isn't in a product yet (sandbox)."),
                                        "device": dev.get("name") or dev["id"],
                                        "platform": platform_name(platform),
                                        "error": getattr(self, "_error", "")})

    # --- API user and entry -------------------------------------------------------------

    async def _finish(self) -> ConfigFlowResult:
        product = self._product
        pid = product["id"]
        old: ConfigEntry | None = None
        if self.source == SOURCE_REAUTH:
            old = self._get_reauth_entry()
        elif self.source == SOURCE_RECONFIGURE:
            old = self._get_reconfigure_entry()
        else:
            await self.async_set_unique_id(f"product_{pid}")
            existing = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, f"product_{pid}")
            if existing:  # product already set up: just add the device to it
                await self._done()
                devices = list(existing.options.get(CONF_DEVICES, []))
                if self._device and self._device["id"] not in devices:
                    devices.append(self._device["id"])
                    self.hass.config_entries.async_update_entry(
                        existing, options={**existing.options, CONF_DEVICES: devices})
                    return self.async_abort(reason="device_added")
                return self.async_abort(reason="already_configured")

        try:
            username, token = await self._account.create_api_user(pid, api_user_name(self.hass))
            if old and old.data.get(CONF_API_USER) and old.data.get(CONF_PRODUCT_ID):
                await self._account.delete_api_user(old.data[CONF_PRODUCT_ID], old.data[CONF_API_USER])
        except ParticleError:
            await self._done()
            return self.async_abort(reason="api_user_failed")
        await self._done()

        data = {CONF_ACCESS_TOKEN: token, CONF_PRODUCT_ID: pid, CONF_PRODUCT_NAME: product.get("name"),
                CONF_PRODUCT_SLUG: product.get("slug"), CONF_API_USER: username}
        title = product.get("name") or f"Product {pid}"

        if old is None:
            return self.async_create_entry(
                title=title, data=data,
                options={CONF_DEVICES: [self._device["id"]], CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL})

        # Reconfigure / reauth: keep the entry (entities, HomeKit, history), swap the access.
        devices = list(old.options.get(CONF_DEVICES, []))
        if self._device:
            if old.data.get(CONF_PRODUCT_ID) != pid:
                # Only devices of the new product can stay.
                in_product = {d["id"] for d in self._devices if d.get("product_id") == pid}
                devices = [d for d in devices if d in in_product]
            if self._device["id"] not in devices:
                devices.append(self._device["id"])
        self.hass.config_entries.async_update_entry(
            old, unique_id=f"product_{pid}", title=title,
            options={**old.options, CONF_DEVICES: devices})
        return self.async_update_reload_and_abort(
            old, data=data, reason="reauth_successful" if self.source == SOURCE_REAUTH else "reconfigure_successful")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ParticleOptionsFlow()


class _Retry(Exception):
    pass


class ParticleOptionsFlow(LoginSteps, OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["devices"]
        if self.config_entry.data.get(CONF_API_USER) and self.config_entry.data.get(CONF_ACCESS_TOKEN):
            menu.append("revoke")
        if len(menu) == 1:
            return await self.async_step_devices()
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_devices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={
                **self.config_entry.options,
                CONF_DEVICES: user_input[CONF_DEVICES],
                CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL])})
        data = self.config_entry.data
        client = ParticleClient(async_get_clientsession(self.hass), data.get(CONF_ACCESS_TOKEN, ""),
                                data.get(CONF_PRODUCT_ID))
        try:
            devices = await client.devices()
        except ParticleAuthError:
            return self.async_abort(reason="invalid_auth")
        except ParticleError:
            return self.async_abort(reason="cannot_connect")
        selected = self.config_entry.options.get(CONF_DEVICES, [])
        known = {d["id"] for d in devices}
        devices += [{"id": s, "name": s} for s in selected if s not in known]
        opts = [SelectOptionDict(value=d["id"], label=(
                    f"{d.get('name') or d['id']} ({d['id'][-6:]}, {'online' if d.get('online') else 'offline'})"))
                for d in sorted(devices, key=lambda d: (not d.get("online"), (d.get("name") or "").lower()))]
        return self.async_show_form(step_id="devices", data_schema=vol.Schema({
            vol.Required(CONF_DEVICES, default=selected): SelectSelector(
                SelectSelectorConfig(options=opts, multiple=True)),
            vol.Required(CONF_POLL_INTERVAL, default=self.config_entry.options.get(
                CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)): NumberSelector(
                NumberSelectorConfig(min=30, max=86400, step=1, unit_of_measurement="s")),
        }), description_placeholders={"product": data.get(CONF_PRODUCT_NAME) or "the account"})

    async def async_step_revoke(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_login()

    def _login_placeholders(self) -> dict[str, str]:
        d = self.config_entry.data
        return {"why": (f"Log in as the owner to delete API user {d.get(CONF_API_USER)} from product "
                        f"{d.get(CONF_PRODUCT_NAME)}. Particle doesn't let an API user remove itself.")}

    async def _logged_in(self) -> ConfigFlowResult:
        d = self.config_entry.data
        try:
            await self._account.delete_api_user(d[CONF_PRODUCT_ID], d[CONF_API_USER])
        except ParticleError:
            await self._done()
            return self.async_abort(reason="revoke_failed")
        await self._done()
        # Keep the entry and its entities; it now waits for re-authentication.
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**d, CONF_ACCESS_TOKEN: "", CONF_API_USER: None})
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return self.async_abort(reason="revoked")
