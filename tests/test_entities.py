"""End-to-end tests for device-declared entities, with a fake Particle Cloud.

A device's publishes (ha/switch/..., ha/light/... and so on) go into the real
integration; the tests check the entities Home Assistant creates and the exact
commands sent back to the device.

Run: pip install pytest-homeassistant-custom-component && pytest tests/
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.particle.const import DOMAIN

DEV = "dev1"


class FakeCloud:
    """Stands in for ParticleClient: one product, one device, an event queue."""

    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.published: list[tuple[str, str]] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.returns: dict[str, int] = {}

    async def devices(self):
        return [await self.device(DEV)]

    async def device(self, device_id):
        return {"id": DEV, "name": "Desk", "online": True, "platform_id": 32,
                "variables": {}, "functions": ["ha"], "system_firmware_version": "6.3.0"}

    async def variable(self, device_id, name):
        return 0

    async def call(self, device_id, name, arg=""):
        self.calls.append((device_id, name, arg))
        return self.returns.get(arg, 0)

    async def publish(self, name, data="", ttl=60):
        self.published.append((name, data))
        if name == "hass-ping":  # the real cloud echoes our own events
            await self.queue.put((name, {"data": data, "coreid": "api"}))

    async def events(self):
        yield "", {}
        while True:
            yield await self.queue.get()

    async def device_publish(self, name, data) -> None:
        await self.queue.put((name, {"data": data if isinstance(data, str) else json.dumps(data),
                                     "coreid": DEV, "published_at": "2026-11-22T00:00:00Z"}))


async def flush(hass: HomeAssistant) -> None:
    for _ in range(10):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


@pytest.fixture
async def cloud(hass: HomeAssistant):
    fake = FakeCloud()
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test product", unique_id="product_1",
        data={"access_token": "t", "product_id": 1, "product_name": "Test product",
              "product_slug": "test-product", "api_user": "ha+x@api.particle.io"},
        options={"devices": [DEV], "poll_interval": 300})
    entry.add_to_hass(hass)
    with patch("custom_components.particle.ParticleClient", return_value=fake):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await flush(hass)
        fake.entry = entry
        yield fake
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_switch(hass, cloud):
    await cloud.device_publish("ha/switch/chime", {"n": "Chime", "on": 1, "cmd": "ha"})
    await flush(hass)
    assert hass.states.get("switch.desk_chime").state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.desk_chime"}, blocking=True)
    assert cloud.calls[-1] == (DEV, "ha", "switch/chime=0")
    assert hass.states.get("switch.desk_chime").state == "off"
    await cloud.device_publish("ha/switch/chime", {"n": "Chime", "on": 1, "cmd": "ha"})  # local change
    await flush(hass)
    assert hass.states.get("switch.desk_chime").state == "on"


async def test_binary_sensor(hass, cloud):
    await cloud.device_publish("ha/binary/door", {"n": "Door", "on": 0, "dc": "door"})
    await cloud.device_publish("ha/binary/motion", "1")  # plain value works too
    await flush(hass)
    door = hass.states.get("binary_sensor.desk_door")
    assert door.state == "off" and door.attributes["device_class"] == "door"
    assert hass.states.get("binary_sensor.desk_motion").state == "on"


async def test_number(hass, cloud):
    await cloud.device_publish("ha/number/hold_time",
                               {"n": "Hold time", "v": 0.8, "min": 0.3, "max": 3, "step": 0.1, "u": "s", "cmd": "ha"})
    await flush(hass)
    st = hass.states.get("number.desk_hold_time")
    assert float(st.state) == 0.8 and st.attributes["min"] == 0.3 and st.attributes["unit_of_measurement"] == "s"
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": "number.desk_hold_time", "value": 1.5}, blocking=True)
    assert cloud.calls[-1] == (DEV, "ha", "number/hold_time=1.5")
    assert float(hass.states.get("number.desk_hold_time").state) == 1.5


async def test_event(hass, cloud):
    # Announced at boot (no "t"): the entity exists, nothing fired yet.
    await cloud.device_publish("ha/event/button1", {"n": "Button 1", "ts": ["press", "double", "hold"]})
    await flush(hass)
    st = hass.states.get("event.desk_button_1")
    assert st.state == "unknown" and st.attributes["event_types"] == ["press", "double", "hold"]
    fired = []
    hass.bus.async_listen("state_changed", lambda e: fired.append(e.data["new_state"].attributes.get("event_type"))
                          if e.data["entity_id"] == "event.desk_button_1" else None)
    await cloud.device_publish("ha/event/button1", {"n": "Button 1", "t": "double", "ts": ["press", "double", "hold"]})
    await flush(hass)
    assert fired == ["double"]
    assert hass.states.get("event.desk_button_1").attributes["event_type"] == "double"


async def test_light_with_command_function(hass, cloud):
    state = {"n": "Ring", "on": 1, "bri": 128, "rgb": "ff8c32", "fx": "solid",
             "fxs": ["solid", "spin", "rainbow", "pulse"], "cmd": "ha"}
    await cloud.device_publish("ha/light/ring", state)
    await flush(hass)
    light = hass.states.get("light.desk_ring")
    assert light.state == "on" and light.attributes["effect"] == "solid"
    await hass.services.async_call("light", "turn_on",
                                   {"entity_id": "light.desk_ring", "effect": "rainbow", "brightness": 40}, blocking=True)
    assert cloud.calls[-2:] == [(DEV, "ha", "light/ring/fx=rainbow"), (DEV, "ha", "light/ring/bri=40")]
    await hass.services.async_call("light", "turn_on",
                                   {"entity_id": "light.desk_ring", "rgb_color": [0, 255, 0]}, blocking=True)
    assert cloud.calls[-1] == (DEV, "ha", "light/ring/rgb=00ff00")
    await hass.services.async_call("light", "turn_off", {"entity_id": "light.desk_ring"}, blocking=True)
    assert cloud.calls[-1] == (DEV, "ha", "light/ring/on=0")
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.desk_ring_pattern", "option": "pulse"}, blocking=True)
    assert cloud.calls[-1] == (DEV, "ha", "light/ring/fx=pulse")


async def test_rejected_command_is_an_error(hass, cloud):
    from homeassistant.exceptions import HomeAssistantError
    await cloud.device_publish("ha/number/auto_off", {"n": "Auto off", "v": 0, "min": 0, "max": 120, "step": 1, "cmd": "ha"})
    await flush(hass)
    cloud.returns["number/auto_off=50"] = -1
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("number", "set_value",
                                       {"entity_id": "number.desk_auto_off", "value": 50}, blocking=True)


async def test_sensors_and_resync(hass, cloud):
    await cloud.device_publish("ha/temperature", {"v": 21.4, "u": "°C", "dc": "temperature", "sc": "measurement"})
    await flush(hass)
    t = hass.states.get("sensor.desk_temperature")
    assert float(t.state) == 21.4 and t.attributes["unit_of_measurement"] == "°C"
    assert ("hass-sync", "") in cloud.published  # asked devices to resend on connect


async def test_entities_survive_a_reload(hass, cloud):
    await cloud.device_publish("ha/switch/night", {"n": "Night mode", "on": 1, "cmd": "ha"})
    await cloud.device_publish("ha/event/button2", {"n": "Button 2", "ts": ["press"]})
    await flush(hass)
    await hass.config_entries.async_reload(cloud.entry.entry_id)
    await flush(hass)
    # Re-created from storage before the device says anything; the switch keeps its state.
    assert hass.states.get("switch.desk_night_mode").state == "on"
    assert hass.states.get("event.desk_button_2") is not None
