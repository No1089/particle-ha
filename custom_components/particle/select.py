"""Pattern picker for lights that list effects (`fxs` in ha/light).

The light entity already has these as effects, but HomeKit has no effects: Apple
Home gets a select as one switch per option, so patterns can be picked from the
Home app, scenes and Siri.
"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api import ParticleError
from .const import DOMAIN, SIGNAL_LIGHT
from .light import DEFAULT_FN, is_light_key


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    data = entry.runtime_data
    known: dict[tuple[str, str], ParticlePattern] = {}

    def _make(did: str, key: str, state: dict) -> ParticlePattern:
        ent = ParticlePattern(data.client, did, key, state)
        known[(did, key)] = ent
        return ent

    async_add_entities(
        _make(did, key, meta["light"])
        for did, keys in data.ha_keys.items() if did in data.device_ids
        for key, meta in keys.items() if is_light_key(key) and (meta.get("light") or {}).get("fxs")
    )

    @callback
    def _state(did: str, key: str, state: dict) -> None:
        ent = known.get((did, key))
        if ent is not None:
            ent.apply(state, write=True)
        elif state.get("fxs"):
            async_add_entities([_make(did, key, state)])

    entry.async_on_unload(async_dispatcher_connect(
        hass, SIGNAL_LIGHT.format(entry_id=entry.entry_id), _state))


class ParticlePattern(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:auto-fix"

    def __init__(self, client, device_id: str, key: str, state: dict) -> None:
        self._client = client
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_ha_{key}_pattern"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})
        sub = "" if key == "light" else key.split("/", 1)[1].replace("_", " ").capitalize() + " "
        self._attr_name = f"{sub}Pattern".strip()
        self._fn = dict(DEFAULT_FN)
        self._attr_options = []
        self._attr_current_option = None
        self.apply(state, write=False)

    @callback
    def apply(self, s: dict, write: bool) -> None:
        self._fn.update(s.get("fn") or {})
        if s.get("fxs"):
            self._attr_options = list(s["fxs"])
        fx = s.get("fx")
        # A mode outside the list (e.g. the Snowflake's "test") shows as no selection.
        if fx is not None:
            self._attr_current_option = fx if fx in self._attr_options else None
        if write:
            self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        try:
            rv = await self._client.call(self.device_id, self._fn["fx"], option)
        except ParticleError as err:
            raise HomeAssistantError(str(err)) from err
        if rv is not None and rv < 0:
            raise HomeAssistantError(f"the device rejected pattern {option!r}")
        self.apply({"fx": option}, write=True)  # the device's ha/light confirms

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._attr_current_option is None and (last := await self.async_get_last_state()):
            if last.state in self._attr_options:
                self._attr_current_option = last.state
