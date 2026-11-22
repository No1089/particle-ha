"""Lights: devices that publish `ha/light` (or `ha/light/<name>`) with their state.

State payload (JSON, all fields but "on" optional):
  {"on": 1, "bri": 0-255, "rgb": "ff8800", "fx": "rainbow", "fxs": ["warm", "rainbow", ...],
   "fn": {"power": "mode", "bri": "brightness", "rgb": "color", "fx": "mode"}}

Commands go to the device's cloud functions; the defaults are the names in "fn" above:
  power function gets "on" / "off", the effect function an effect name,
  brightness "0".."255", colour "rrggbb".
The device confirms by publishing its new state; until then HA shows the requested one.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (ATTR_BRIGHTNESS, ATTR_EFFECT, ATTR_RGB_COLOR,
                                            ColorMode, LightEntity, LightEntityFeature)
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api import ParticleError
from .const import DOMAIN, SIGNAL_LIGHT

DEFAULT_FN = {"power": "mode", "bri": "brightness", "rgb": "color", "fx": "mode"}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    data = entry.runtime_data
    known: dict[tuple[str, str], ParticleLight] = {}

    def _make(did: str, key: str, state: dict) -> ParticleLight:
        ent = ParticleLight(data.client, did, key, state)
        known[(did, key)] = ent
        return ent

    async_add_entities(
        _make(did, key, meta.get("light") or {})
        for did, keys in data.ha_keys.items() if did in data.device_ids
        for key, meta in keys.items() if is_light_key(key)
    )

    @callback
    def _state(did: str, key: str, state: dict) -> None:
        ent = known.get((did, key))
        if ent is None:
            async_add_entities([_make(did, key, state)])
        else:
            ent.apply(state, write=True)

    entry.async_on_unload(async_dispatcher_connect(
        hass, SIGNAL_LIGHT.format(entry_id=entry.entry_id), _state))


def is_light_key(key: str) -> bool:
    return key == "light" or key.startswith("light/")


class ParticleLight(LightEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, client, device_id: str, key: str, state: dict) -> None:
        self._client = client
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_ha_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})
        # "light" -> the device's own name; "light/strip" -> "Strip" (or the payload's "n")
        self._key = key
        self._attr_name = None if key == "light" else key.split("/", 1)[1].replace("_", " ").capitalize()
        self._fn = dict(DEFAULT_FN)
        self._cmd: str | None = None  # one command function for all fields (HomeAssistant.h)
        self._attr_is_on = None
        self._attr_color_mode = ColorMode.BRIGHTNESS
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self.apply(state, write=False)

    @callback
    def apply(self, s: dict, write: bool) -> None:
        if not s:
            return
        self._fn.update(s.get("fn") or {})
        if s.get("cmd"):
            self._cmd = s["cmd"]
        if s.get("n") and self._key != "light":
            self._attr_name = s["n"]
        if "on" in s:
            self._attr_is_on = bool(s["on"])
        if "bri" in s:
            self._attr_brightness = max(0, min(255, int(s["bri"])))
        if "fxs" in s:
            self._attr_effect_list = list(s["fxs"])
        if "fx" in s:
            self._attr_effect = s["fx"]
        if "rgb" in s:
            h = str(s["rgb"]).lstrip("#")
            try:
                self._attr_rgb_color = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            except ValueError:
                pass
        mode = ColorMode.RGB if "rgb" in s or self._attr_rgb_color else ColorMode.BRIGHTNESS
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}
        self._attr_supported_features = LightEntityFeature.EFFECT if self._attr_effect_list else LightEntityFeature(0)
        if write:
            self.async_write_ha_state()

    async def _call(self, role: str, arg: str) -> int:
        if self._cmd:
            # "light/<key>/<field>=<value>"; power is on=1/0
            field = {"power": "on", "bri": "bri", "rgb": "rgb", "fx": "fx"}[role]
            if role == "power":
                arg = "1" if arg == "on" else "0"
            fn, arg = self._cmd, f"{self._key}/{field}={arg}"
        else:
            fn = self._fn.get(role)
        if not fn:
            raise HomeAssistantError(f"this light has no {role} function")
        try:
            rv = await self._client.call(self.device_id, fn, arg)
        except ParticleError as err:
            raise HomeAssistantError(str(err)) from err
        if rv is not None and rv < 0:
            raise HomeAssistantError(f"{fn}({arg!r}) was rejected by the device ({rv})")
        return rv

    async def async_turn_on(self, **kw: Any) -> None:
        new: dict = {"on": 1}
        # Effect first, so a colour sent with it tints the new effect (devices may
        # treat a colour as "tint this pattern" or as "switch to a solid colour").
        if ATTR_EFFECT in kw:
            await self._call("fx", kw[ATTR_EFFECT])
            new["fx"] = kw[ATTR_EFFECT]
        if ATTR_RGB_COLOR in kw:
            r, g, b = kw[ATTR_RGB_COLOR]
            await self._call("rgb", f"{r:02x}{g:02x}{b:02x}")
            new["rgb"] = f"{r:02x}{g:02x}{b:02x}"
        if not (ATTR_EFFECT in kw or ATTR_RGB_COLOR in kw) and (not self._attr_is_on or not kw):
            await self._call("power", "on")
        if ATTR_BRIGHTNESS in kw:
            await self._call("bri", str(kw[ATTR_BRIGHTNESS]))
            new["bri"] = kw[ATTR_BRIGHTNESS]
        self.apply(new, write=True)  # optimistic; the device's ha/light confirms

    async def async_turn_off(self, **kw: Any) -> None:
        await self._call("power", "off")
        self.apply({"on": 0}, write=True)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._attr_is_on is None and (last := await self.async_get_last_state()):
            a = last.attributes
            self._attr_is_on = last.state == STATE_ON
            if a.get("brightness") is not None:
                self._attr_brightness = a["brightness"]
            if a.get("effect_list"):
                self._attr_effect_list = a["effect_list"]
                self._attr_supported_features = LightEntityFeature.EFFECT
            if a.get("effect"):
                self._attr_effect = a["effect"]
            if a.get("rgb_color"):
                self._attr_rgb_color = tuple(a["rgb_color"])
                self._attr_color_mode = ColorMode.RGB
                self._attr_supported_color_modes = {ColorMode.RGB}
