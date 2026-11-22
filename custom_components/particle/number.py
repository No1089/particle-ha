"""Numbers: ha/number/<key> with {"v", "min", "max", "step", "u"}; commands "number/<key>=<v>"."""
from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .push import PushEntity, setup_push_platform


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    setup_push_platform(hass, entry, async_add_entities, "number", ParticleNumber)


class ParticleNumber(PushEntity, RestoreNumber):
    _attr_mode = NumberMode.AUTO

    def apply(self, state: dict) -> None:
        for attr, field in (("_attr_native_min_value", "min"), ("_attr_native_max_value", "max"),
                            ("_attr_native_step", "step"), ("_attr_native_value", "v")):
            if isinstance(state.get(field), (int, float)):
                setattr(self, attr, float(state[field]))
        if state.get("u"):
            self._attr_native_unit_of_measurement = state["u"]
        if state.get("mode") in ("box", "slider"):
            self._attr_mode = NumberMode(state["mode"])

    async def async_set_native_value(self, value: float) -> None:
        text = f"{value:g}"
        await self.command(text)
        self._attr_native_value = value
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.first is not None:
            self.update_from_device(self.first, write=False)
        elif getattr(self, "_attr_native_value", None) is None and (last := await self.async_get_last_number_data()):
            self._attr_native_value = last.native_value
