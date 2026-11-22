"""Events: ha/event/<key> with {"t": "<type>", "ts": [types]}; a publish without "t" just
announces the entity (the helper does that at boot), so it exists before the first press."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .push import PushEntity, setup_push_platform


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    setup_push_platform(hass, entry, async_add_entities, "event", ParticleEvent)


class ParticleEvent(PushEntity, EventEntity):
    _attr_event_types = ["press"]

    def apply(self, state: dict) -> None:
        types = state.get("ts")
        if isinstance(types, list) and types:
            self._attr_event_types = [str(t) for t in types]

    @callback
    def update_from_device(self, state: dict, write: bool) -> None:
        super().update_from_device(state, write=False)
        t = state.get("t")
        if write and t:
            if t not in self._attr_event_types:
                self._attr_event_types = [*self._attr_event_types, t]
            self._trigger_event(t)
        if write:
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.first is not None and self.first.get("t"):
            t = self.first["t"]
            if t not in self._attr_event_types:
                self._attr_event_types = [*self._attr_event_types, t]
            self._trigger_event(t)  # the publish that created it was a real press
        self.first = None
