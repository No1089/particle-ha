"""UI setup: paste a Particle access token, pick devices."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (NumberSelector, NumberSelectorConfig, SelectOptionDict,
                                            SelectSelector, SelectSelectorConfig, TextSelector,
                                            TextSelectorConfig, TextSelectorType)

from .api import ParticleAuthError, ParticleClient, ParticleError
from .const import CONF_DEVICES, CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL, DOMAIN

TOKEN_SCHEMA = vol.Schema({vol.Required(CONF_ACCESS_TOKEN): TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD))})


def _device_options(devices: list[dict]) -> list[SelectOptionDict]:
    out = []
    for d in sorted(devices, key=lambda d: (not d.get("online"), (d.get("name") or "").lower())):
        state = "online" if d.get("online") else "offline"
        out.append(SelectOptionDict(value=d["id"], label=f"{d.get('name') or d['id']} ({d['id'][-6:]}, {state})"))
    return out


def _devices_schema(devices: list[dict], selected: list[str], interval: int) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_DEVICES, default=selected): SelectSelector(
            SelectSelectorConfig(options=_device_options(devices), multiple=True)),
        vol.Required(CONF_POLL_INTERVAL, default=interval): NumberSelector(
            NumberSelectorConfig(min=30, max=86400, step=1, unit_of_measurement="s")),
    })


async def _validate(hass, token: str) -> tuple[dict, list[dict]]:
    client = ParticleClient(async_get_clientsession(hass), token)
    return await client.user(), await client.devices()


class ParticleConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._token = ""
        self._user: dict = {}
        self._devices: list[dict] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._token = user_input[CONF_ACCESS_TOKEN].strip()
            try:
                self._user, self._devices = await _validate(self.hass, self._token)
            except ParticleAuthError:
                errors["base"] = "invalid_auth"
            except ParticleError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(self._user.get("username"))
                self._abort_if_unique_id_configured()
                return await self.async_step_devices()
        return self.async_show_form(step_id="user", data_schema=TOKEN_SCHEMA, errors=errors)

    async def async_step_devices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self._user.get("username", "Particle"),
                data={CONF_ACCESS_TOKEN: self._token},
                options={CONF_DEVICES: user_input[CONF_DEVICES],
                         CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL])},
            )
        return self.async_show_form(
            step_id="devices",
            data_schema=_devices_schema(self._devices, [], DEFAULT_POLL_INTERVAL),
            description_placeholders={"count": str(len(self._devices))},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_ACCESS_TOKEN].strip()
            try:
                user, _ = await _validate(self.hass, token)
            except ParticleAuthError:
                errors["base"] = "invalid_auth"
            except ParticleError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user.get("username"))
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_ACCESS_TOKEN: token})
        return self.async_show_form(step_id="reauth_confirm", data_schema=TOKEN_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ParticleOptionsFlow()


class ParticleOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={
                CONF_DEVICES: user_input[CONF_DEVICES],
                CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL])})
        try:
            _, devices = await _validate(self.hass, self.config_entry.data[CONF_ACCESS_TOKEN])
        except ParticleError:
            return self.async_abort(reason="cannot_connect")
        selected = self.config_entry.options.get(CONF_DEVICES, [])
        known = {d["id"] for d in devices}
        devices += [{"id": s, "name": s} for s in selected if s not in known]
        return self.async_show_form(step_id="init", data_schema=_devices_schema(
            devices, selected, self.config_entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)))
