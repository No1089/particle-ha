"""Entities a device declares by publishing ha/<kind>/<key> (switch, binary, number, event).

The integration creates them on the first publish, keeps them across restarts (the key
and last state are stored), and sends commands to the device's command function
(`cmd` in the payload, default "ha") as "<kind>/<key>=<value>".
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import ParticleError
from .const import DEFAULT_CMD, DOMAIN, SIGNAL_ENTITY


def setup_push_platform(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback,
                        kind: str, cls: type[PushEntity]) -> None:
    data = entry.runtime_data
    known: dict[tuple[str, str], PushEntity] = {}

    def _make(did: str, key: str, state: dict) -> PushEntity:
        ent = cls(data.client, did, key, state)
        known[(did, key)] = ent
        return ent

    async_add_entities(
        _make(did, key, meta.get("state") or {})
        for did, keys in data.ha_keys.items() if did in data.device_ids
        for key, meta in keys.items() if meta.get("entity") == kind
    )

    @callback
    def _state(did: str, k: str, key: str, state: dict) -> None:
        if k != kind or did not in data.device_ids:
            return
        ent = known.get((did, key))
        if ent is None:
            ent = _make(did, key, state)
            ent.first = state
            async_add_entities([ent])
        else:
            ent.update_from_device(state, write=True)

    entry.async_on_unload(async_dispatcher_connect(
        hass, SIGNAL_ENTITY.format(entry_id=entry.entry_id), _state))


def pretty(key: str) -> str:
    return key.split("/", 1)[-1].replace("_", " ").strip().capitalize()


class PushEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, client, device_id: str, key: str, state: dict) -> None:
        self._client = client
        self.device_id = device_id
        self.key = key  # "<kind>/<name>"
        self.first: dict | None = None  # state that announced it, applied once added
        self._cmd = DEFAULT_CMD
        self._attr_unique_id = f"{device_id}_ha_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})
        self._attr_name = state.get("n") or pretty(key)
        self.update_from_device(state, write=False)

    @callback
    def update_from_device(self, state: dict, write: bool) -> None:
        if state.get("n"):
            self._attr_name = state["n"]
        if state.get("cmd"):
            self._cmd = state["cmd"]
        self.apply(state)
        if write:
            self.async_write_ha_state()

    def apply(self, state: dict) -> None:
        """Copy the device's state onto the entity's attributes."""

    async def command(self, value: Any) -> int:
        try:
            rv = await self._client.call(self.device_id, self._cmd, f"{self.key}={value}")
        except ParticleError as err:
            raise HomeAssistantError(str(err)) from err
        if rv is not None and rv < 0:
            raise HomeAssistantError(f"The device rejected {self.key}={value}")
        return rv
