"""Sensors: polled cloud variables, and live `ha/<key>` events."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (RestoreSensor, SensorDeviceClass, SensorEntity,
                                             SensorStateClass)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_HA_VALUE
from .entity import ParticleEntity

NUMERIC = {"int32", "double", "int", "float"}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    data = entry.runtime_data
    async_add_entities(
        ParticleVariableSensor(data.coordinator, did, name, vtype)
        for did, d in data.coordinator.data.items()
        for name, vtype in (d["info"].get("variables") or {}).items()
    )

    # ha/<key> sensors: re-create the ones seen before, add new ones as they appear.
    known: dict[tuple[str, str], ParticleHaSensor] = {}

    def _make(did: str, key: str, meta: dict) -> ParticleHaSensor:
        ent = ParticleHaSensor(did, key, meta)
        known[(did, key)] = ent
        return ent

    async_add_entities(
        _make(did, key, meta)
        for did, keys in data.ha_keys.items() if did in data.device_ids
        for key, meta in keys.items()
    )

    @callback
    def _value(did: str, key: str, value: Any, meta: dict) -> None:
        ent = known.get((did, key))
        if ent is None:
            ent = _make(did, key, meta)
            ent.pending = value
            async_add_entities([ent])
        else:
            ent.set_value(value, meta)

    entry.async_on_unload(async_dispatcher_connect(
        hass, SIGNAL_HA_VALUE.format(entry_id=entry.entry_id), _value))


class ParticleVariableSensor(ParticleEntity, SensorEntity):
    def __init__(self, coordinator, device_id: str, name: str, vtype: str) -> None:
        super().__init__(coordinator, device_id, f"var_{name}")
        self._var = name
        self._attr_name = name
        if vtype in NUMERIC:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        return super().available and self.online and self._var in self._values

    @property
    def _values(self) -> dict:
        return (self.coordinator.data or {}).get(self.device_id, {}).get("values", {})

    @property
    def native_value(self):
        v = self._values.get(self._var)
        if isinstance(v, str) and len(v) > 255:
            return v[:255]
        return v


class ParticleHaSensor(RestoreSensor):
    """Value pushed by the device as event `ha/<key>`. Keeps its last value across restarts."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device_id: str, key: str, meta: dict) -> None:
        self.device_id = device_id
        self.key = key
        self.pending: Any = None
        self._attr_unique_id = f"{device_id}_ha_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})
        self._apply_meta(meta)

    def _apply_meta(self, meta: dict) -> None:
        self._attr_name = meta.get("n") or key_to_name(self.key)
        self._attr_native_unit_of_measurement = meta.get("u")
        self._attr_icon = meta.get("i")
        dc = meta.get("dc")
        try:
            self._attr_device_class = SensorDeviceClass(dc) if dc else None
        except ValueError:
            self._attr_device_class = None
        sc = meta.get("sc")
        try:
            self._attr_state_class = SensorStateClass(sc) if sc else None
        except ValueError:
            self._attr_state_class = None

    @callback
    def set_value(self, value: Any, meta: dict) -> None:
        self._apply_meta(meta)
        self._store(value)
        self.async_write_ha_state()

    def _store(self, value: Any) -> None:
        self._attr_native_value = value[:255] if isinstance(value, str) else value
        if self._attr_state_class is None and isinstance(value, (int, float)) and not isinstance(value, bool) \
                and self._attr_device_class not in (SensorDeviceClass.TIMESTAMP, SensorDeviceClass.ENUM):
            self._attr_state_class = SensorStateClass.MEASUREMENT

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.pending is not None:
            self._store(self.pending)  # first value; HA writes the state after this
            self.pending = None
        elif (last := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = last.native_value


def key_to_name(key: str) -> str:
    return key.replace("_", " ").replace("/", " ").strip().capitalize()
