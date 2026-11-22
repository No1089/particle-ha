"""Particle Cloud integration for Home Assistant.

- Every selected device becomes an HA device with an "online" sensor.
- Cloud variables become sensors (polled), cloud functions become buttons and the
  particle.call_function action.
- Events from the devices arrive live over the Particle SSE stream (no inbound
  webhook needed) and are fired on the HA bus as `particle_event`.
- Events named `ha/<key>` become sensors on their own (see firmware/HomeAssistant.h).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, Platform
from homeassistant.core import (HomeAssistant, ServiceCall, ServiceResponse,
                                SupportsResponse, callback)
from homeassistant.exceptions import (ConfigEntryAuthFailed, ConfigEntryNotReady,
                                      HomeAssistantError, ServiceValidationError)
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .stream import EventStream
from .api import ParticleAuthError, ParticleClient, ParticleError
from .const import (BUS_EVENT, CONF_API_USER, CONF_DEVICES, CONF_PRODUCT_ID, CONF_PRODUCT_NAME,
                    CONF_PRODUCT_SLUG, PLATFORM_NAMES, CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL,
                    DOMAIN, HA_PREFIX, SIGNAL_HA_VALUE, SIGNAL_LIGHT, STORE_VERSION,
                    SYNC_EVENT)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.LIGHT, Platform.SELECT, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)



@dataclass
class ParticleData:
    client: ParticleClient
    coordinator: DataUpdateCoordinator
    device_ids: list[str]
    store: Store
    ha_keys: dict[str, dict[str, dict]] = field(default_factory=dict)  # device -> key -> meta
    shape: dict[str, tuple] = field(default_factory=dict)  # device -> (variables, functions)
    stream: EventStream | None = None


type ParticleConfigEntry = ConfigEntry[ParticleData]


def parse_ha_payload(raw: str | None) -> tuple[Any, dict]:
    """`ha/<key>` data: plain value, or JSON {"v": value, "u": unit, "dc": device class,
    "sc": state class, "n": name, "i": icon}. Returns (value, meta)."""
    raw = (raw or "").strip()
    meta: dict = {}
    value: Any = raw
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and "v" in obj:
            value = obj["v"]
            meta = {k: obj[k] for k in ("u", "dc", "sc", "n", "i") if k in obj}
    if isinstance(value, str):
        try:
            value = float(value) if any(c in value for c in ".eE") else int(value)
        except ValueError:
            pass
    return value, meta


async def async_setup_entry(hass: HomeAssistant, entry: ParticleConfigEntry) -> bool:
    token = entry.data.get(CONF_ACCESS_TOKEN)
    if not token or not entry.data.get(CONF_PRODUCT_ID):  # revoked from the options flow
        raise ConfigEntryAuthFailed("Particle access was revoked; re-authenticate to create a new API user")
    client = ParticleClient(async_get_clientsession(hass), token, entry.data[CONF_PRODUCT_ID])
    device_ids: list[str] = entry.options.get(CONF_DEVICES, entry.data.get(CONF_DEVICES, []))
    interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    async def _update() -> dict[str, dict]:
        out: dict[str, dict] = {}
        for did in device_ids:
            try:
                info = await client.device(did)
            except ParticleAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except ParticleError as err:
                _LOGGER.debug("device %s: %s", did, err)
                if coordinator.data and did in coordinator.data:
                    out[did] = coordinator.data[did]  # keep last known
                continue
            values: dict[str, Any] = {}
            if info.get("online"):
                for name in info.get("variables") or {}:
                    try:
                        values[name] = await client.variable(did, name)
                    except ParticleError as err:
                        _LOGGER.debug("variable %s/%s: %s", did, name, err)
            out[did] = {"info": info, "values": values}
            _check_shape(did, info)
        if device_ids and not out:
            raise UpdateFailed("no Particle device could be read")
        return out

    @callback
    def _check_shape(did: str, info: dict) -> None:
        """Firmware with new variables/functions: reload to create their entities."""
        shape = (tuple(sorted(info.get("variables") or {})), tuple(sorted(info.get("functions") or [])))
        old = data.shape.get(did)
        data.shape[did] = shape
        if old is not None and old != shape:
            _LOGGER.info("Particle %s changed its variables/functions, reloading", did)
            hass.config_entries.async_schedule_reload(entry.entry_id)

    coordinator = DataUpdateCoordinator(
        hass, _LOGGER, config_entry=entry, name=DOMAIN,
        update_method=_update, update_interval=timedelta(seconds=interval),
    )
    store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    data = ParticleData(client, coordinator, device_ids, store, await store.async_load() or {})
    entry.runtime_data = data

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except Exception as err:  # noqa: BLE001
        raise ConfigEntryNotReady(str(err)) from err

    devreg = dr.async_get(hass)
    devreg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"account_{entry.entry_id}")},
        manufacturer="Particle", name="Particle Cloud",
        model=f"Product {entry.data[CONF_PRODUCT_ID]}, API user",
        entry_type=dr.DeviceEntryType.SERVICE,
        configuration_url=_console_url(entry),
    )
    for did, d in coordinator.data.items():
        info = d["info"]
        devreg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, did)},
            manufacturer="Particle",
            name=info.get("name") or did,
            model=PLATFORM_NAMES.get(info.get("platform_id"), f"Platform {info.get('platform_id')}"),
            sw_version=info.get("system_firmware_version"),
            serial_number=info.get("serial_number"),
            configuration_url=f"https://console.particle.io/devices/{did}",
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    wanted = set(device_ids)

    @callback
    def _on_event(name: str, payload: dict) -> None:
        did = payload.get("coreid")
        if did in wanted:
            _handle_event(hass, entry, did, name, payload)

    async def _on_open(connects: int) -> None:
        # Ask devices to resend their state (HomeAssistant.h answers hass-sync), so
        # values published while the stream was down aren't lost.
        try:
            await client.publish(SYNC_EVENT, "", ttl=60)
        except ParticleError as err:
            _LOGGER.debug("hass-sync publish failed: %s", err)
        if connects > 1:
            await coordinator.async_request_refresh()  # online state, variables

    data.stream = EventStream(hass, entry.entry_id, client, _on_event, _on_open,
                              lambda: entry.async_start_reauth(hass))
    entry.async_create_background_task(hass, data.stream.run(), f"{DOMAIN} event stream")
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: ParticleConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _handle_event(hass: HomeAssistant, entry: ParticleConfigEntry, did: str,
                  name: str, payload: dict) -> None:
    data = entry.runtime_data
    raw = payload.get("data")
    hass.bus.async_fire(BUS_EVENT, {
        "device_id": did, "event": name, "data": raw,
        "published_at": payload.get("published_at"),
    })

    coord = data.coordinator
    if name == "spark/status" and coord.data and did in coord.data:
        coord.data[did]["info"]["online"] = raw == "online"
        coord.async_update_listeners()
        if raw == "online":
            # New firmware may bring new variables/functions. The device sends them
            # (DESCRIBE) just after coming online, so give the cloud a moment.
            async_call_later(hass, 15, lambda _now: hass.async_create_task(coord.async_request_refresh()))
        return

    if name.startswith(HA_PREFIX) and len(name) > len(HA_PREFIX):
        key = name[len(HA_PREFIX):]
        keys = data.ha_keys.setdefault(did, {})
        if key == "light" or key.startswith("light/"):
            try:
                state = json.loads(raw or "{}")
            except ValueError:
                _LOGGER.warning("%s from %s is not JSON: %s", name, did, raw)
                return
            if not isinstance(state, dict):
                return
            meta = {"light": state}
            if keys.get(key) != meta:
                keys[key] = meta
                data.store.async_delay_save(lambda: data.ha_keys, 5)
            async_dispatcher_send(hass, SIGNAL_LIGHT.format(entry_id=entry.entry_id), did, key, state)
            return
        value, meta = parse_ha_payload(raw)
        if keys.get(key) != meta:
            keys[key] = meta
            data.store.async_delay_save(lambda: data.ha_keys, 5)
        async_dispatcher_send(hass, SIGNAL_HA_VALUE.format(entry_id=entry.entry_id),
                              did, key, value, meta)


def _console_url(entry: ConfigEntry) -> str:
    slug = entry.data.get(CONF_PRODUCT_SLUG) or entry.data.get(CONF_PRODUCT_ID)
    return f"https://console.particle.io/{slug}/team" if slug else "https://console.particle.io"


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Removing the entry can't delete the API user (that needs an owner login, see the
    Revoke option), so say where it is."""
    user = entry.data.get(CONF_API_USER)
    if not (user and entry.data.get(CONF_ACCESS_TOKEN)):
        return
    from homeassistant.components import persistent_notification
    persistent_notification.async_create(
        hass,
        f"The Particle integration for **{entry.data.get(CONF_PRODUCT_NAME)}** was removed, but its API "
        f"user `{user}` still exists and its token stays valid. Delete it under "
        f"[Team]({_console_url(entry)}) in the Particle console (look for \"Home Assistant\"). "
        "Next time, use *Configure → Revoke* before removing.",
        title="Particle API user left behind",
        notification_id=f"particle_api_user_{entry.entry_id}",
    )


async def async_unload_entry(hass: HomeAssistant, entry: ParticleConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


# --- actions -------------------------------------------------------------------

def _resolve(hass: HomeAssistant, ha_device_id: str) -> tuple[ParticleConfigEntry, str]:
    dev = dr.async_get(hass).async_get(ha_device_id)
    pid = next((i[1] for i in dev.identifiers if i[0] == DOMAIN), None) if dev else None
    if pid is None:
        raise ServiceValidationError(f"{ha_device_id} is not a Particle device")
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if pid in entry.runtime_data.device_ids:
            return entry, pid
    raise ServiceValidationError(f"Particle device {pid} is not loaded")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async def call_function(call: ServiceCall) -> ServiceResponse:
        entry, pid = _resolve(hass, call.data["device_id"])
        try:
            rv = await entry.runtime_data.client.call(pid, call.data["function"], call.data.get("argument", ""))
        except ParticleError as err:
            raise HomeAssistantError(str(err)) from err
        return {"return_value": rv}

    async def publish_event(call: ServiceCall) -> None:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError("No Particle product is set up")
        # One entry per product: publish into each, so devices in all of them get it.
        failed = []
        for entry in entries:
            try:
                await entry.runtime_data.client.publish(
                    call.data["name"], call.data.get("data", ""), ttl=call.data.get("ttl", 60))
            except ParticleError as err:
                failed.append(f"{entry.title}: {err}")
        if failed:
            raise HomeAssistantError("; ".join(failed))

    hass.services.async_register(
        DOMAIN, "call_function", call_function,
        schema=vol.Schema({
            vol.Required("device_id"): cv.string,
            vol.Required("function"): cv.string,
            vol.Optional("argument", default=""): cv.string,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "publish_event", publish_event,
        schema=vol.Schema({
            vol.Required("name"): cv.string,
            vol.Optional("data", default=""): cv.string,
            vol.Optional("ttl", default=60): vol.All(int, vol.Range(min=1)),
        }),
    )
    return True
