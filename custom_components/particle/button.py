"""Buttons a device declares: ha/button/<key> with {"n": name}; a press sends
"button/<key>=press" to the device's command function (HomeAssistant.h: HA.addButton).

Cloud functions don't get buttons of their own: most take an argument, and an empty
one can do the wrong thing. Call them with the particle.call_function action instead.
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .push import PushEntity, setup_push_platform


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    setup_push_platform(hass, entry, async_add_entities, "button", ParticleButton)


class ParticleButton(PushEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self.command("press")
