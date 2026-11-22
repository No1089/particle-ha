"""Switches: ha/switch/<key> with {"on": 0|1}; commands "switch/<key>=1|0"."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .push import PushEntity, setup_push_platform


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    setup_push_platform(hass, entry, async_add_entities, "switch", ParticleSwitch)


class ParticleSwitch(PushEntity, SwitchEntity, RestoreEntity):
    def apply(self, state: dict) -> None:
        if "on" in state:
            self._attr_is_on = bool(state["on"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.command(1)
        self._attr_is_on = True  # the device's publish confirms
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.command(0)
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.first is not None:
            self.update_from_device(self.first, write=False)
        elif self._attr_is_on is None and (last := await self.async_get_last_state()):
            self._attr_is_on = last.state == STATE_ON
