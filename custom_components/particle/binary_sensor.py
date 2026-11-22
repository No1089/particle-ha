"""Online state per device, plus the event stream's health."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.const import STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, SIGNAL_STREAM
from .entity import ParticleEntity
from .push import PushEntity, setup_push_platform


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    data = entry.runtime_data
    entities: list = [ParticleOnline(data.coordinator, did) for did in data.coordinator.data]
    entities.append(StreamHealth(entry))
    async_add_entities(entities)
    setup_push_platform(hass, entry, async_add_entities, "binary", ParticleBinary)


class ParticleBinary(PushEntity, BinarySensorEntity, RestoreEntity):
    """ha/binary/<key> with {"on": 0|1, "dc": device class}: door, motion, moisture, ..."""

    def apply(self, state: dict) -> None:
        if "on" in state:
            self._attr_is_on = bool(state["on"])
        if state.get("dc"):
            try:
                self._attr_device_class = BinarySensorDeviceClass(state["dc"])
            except ValueError:
                self._attr_device_class = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.first is not None:
            self.update_from_device(self.first, write=False)
        elif self._attr_is_on is None and (last := await self.async_get_last_state()):
            self._attr_is_on = last.state == STATE_ON


class ParticleOnline(ParticleEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Online"

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "online")

    @property
    def is_on(self) -> bool:
        return self.online

    @property
    def extra_state_attributes(self) -> dict:
        i = self.info
        return {k: i.get(k) for k in ("last_heard", "last_ip_address", "system_firmware_version",
                                      "firmware_version", "product_id") if i.get(k) is not None}


class StreamHealth(BinarySensorEntity):
    """On = connected AND the last canary event came back."""

    _attr_has_entity_name = True
    _attr_name = "Event stream"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_event_stream"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"account_{entry.entry_id}")})

    @property
    def _stream(self):
        return self._entry.runtime_data.stream

    @property
    def is_on(self) -> bool:
        return bool(self._stream and self._stream.healthy)

    @property
    def extra_state_attributes(self) -> dict:
        return self._stream.attributes if self._stream else {}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_STREAM.format(entry_id=self._entry.entry_id), self.async_write_ha_state))
