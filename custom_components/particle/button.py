"""A button per cloud function (called with an empty argument).

For arguments use the particle.call_function action.
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import ParticleError
from .entity import ParticleEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    data = entry.runtime_data
    async_add_entities(
        ParticleFunctionButton(data.coordinator, data.client, did, fn)
        for did, d in data.coordinator.data.items()
        for fn in d["info"].get("functions") or []
    )


class ParticleFunctionButton(ParticleEntity, ButtonEntity):
    def __init__(self, coordinator, client, device_id: str, function: str) -> None:
        super().__init__(coordinator, device_id, f"fn_{function}")
        self._client = client
        self._function = function
        self._attr_name = function
        self._attr_icon = "mdi:function-variant"

    @property
    def available(self) -> bool:
        return super().available and self.online

    async def async_press(self) -> None:
        try:
            rv = await self._client.call(self.device_id, self._function, "")
        except ParticleError as err:
            raise HomeAssistantError(str(err)) from err
        self._attr_extra_state_attributes = {"last_return_value": rv}
        self.async_write_ha_state()
