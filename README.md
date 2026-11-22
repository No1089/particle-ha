# particle-ha

<img src="custom_components/particle/brand/icon.png" width="64" align="right" alt="">

Home Assistant custom integration for Particle devices (Photon 2, P2, M-SoM, Boron, Argon, …)
through the Particle Cloud. Set it up from the UI with an access token, pick devices, done.

| On the device | In Home Assistant |
|---|---|
| online / offline | `binary_sensor.<device>_online` (live, from `spark/status`) |
| `Particle.variable("x", …)` | `sensor.<device>_x`, polled every *poll interval* |
| `Particle.function("f", …)` | `button.<device>_f` (empty argument) and action `particle.call_function` (any argument, returns the int) |
| `Particle.publish("ha/<key>", …)` | `sensor.<device>_<key>`, created on first publish, live; keeps its value over HA restarts |
| any other `Particle.publish(...)` | HA bus event `particle_event` (`device_id`, `event`, `data`, `published_at`) for automations |
| `Particle.subscribe("home/", …)` | action `particle.publish_event` sends private events to the devices |
| `Particle.publish("ha/light", {json})` | `light.<device>`: on/off, brightness, RGB colour, effects, plus `select.<device>_pattern` (see below) |

Devices that gain new variables/functions after a firmware update get their entities automatically:
the integration notices the change on the next poll after the device comes online and reloads itself.

## Why the event stream, not webhooks

Particle webhooks would need HA reachable from the internet. Instead the integration holds one
outgoing Server-Sent Events connection to `api.particle.io/v1/devices/events`, so it works behind NAT
with nothing exposed.

### Keeping SSE alive

SSE breaks in two ways, and both are handled (`custom_components/particle/stream.py`):

1. **Dead socket** (Wi-Fi drop, NAT timeout, Particle restart). Particle sends a keepalive about
   every 9 s, so a 60 s read timeout catches it. Reconnects use exponential backoff with jitter
   (5 s → 5 min), and the backoff resets after a connection that stayed healthy.
2. **Zombie stream**: keepalives keep arriving but events stop. Every 5 min the integration publishes
   a private canary event `hass-ping` with a random nonce. If it isn't echoed on its own stream within
   45 s, the connection is torn down and reopened.

After every reconnect the device states and variables are re-read, to catch up on the gap. Events
published *during* a gap are lost (Particle doesn't replay), so `HomeAssistant.h` resends every value
every 15 min. If the stream stays unhealthy for 15 min, a Repairs issue appears; it clears itself
on recovery. Health is visible on `binary_sensor.particle_cloud_event_stream`, with attributes
`connects`, `last_probe_ok`, `last_probe_ms` (canary round trip, ~300 ms) and `last_error`.
A rejected token stops the stream and starts HA's re-auth flow.

`tests/test_stream.py` drives the stream through each failure mode with a fake client:
`<venv with homeassistant>/bin/python tests/test_stream.py`.

## Lights

A device that publishes `ha/light` (or `ha/light/<name>` for several) becomes a light entity:

```json
{"on": 1, "bri": 128, "rgb": "ff6a1e", "fx": "rainbow", "fxs": ["warm", "rainbow", "solid"]}
```

All fields but `on` are optional (`rgb` → colour picker, `fxs` → effect list). HA drives it through
cloud functions: `mode("on"|"off"|<effect>)`, `brightness("0".."255")`, `color("rrggbb")`. Other names
can be given in the payload, e.g. `"fn": {"power": "power", "fx": "effect", "bri": "level", "rgb": "rgb"}`.
A negative return value is shown in HA as an error.

If the payload lists effects (`fxs`), the device also gets a **Pattern** select (`select.<device>_pattern`).
It duplicates the light's effects on purpose: HomeKit has no effects, but HA's HomeKit bridge shows a
select as one switch per option. Add the select to the bridge and the patterns can be picked in the
Home app, in scenes and with Siri ("turn on Twinkle"). The device should publish its state on every
change, including local ones (buttons, touch), so HA follows. First user: the Particle Snowflake
(No1089 snowflake-lights).

## Resync after gaps

Whenever the event stream (re)connects, the integration publishes `hass-sync`. Devices that subscribe
to it (`HA.begin()` in `HomeAssistant.h`, or by hand) send their state again at once, so values
published while the stream was down don't stay stale until the next change.

## Firmware helper

`firmware/HomeAssistant.h` (header only, copy it into `src/`):

```cpp
#include "HomeAssistant.h"

void loop() {
    HA.value("temperature", 21.4, "°C", "temperature", "measurement");
    HA.value("door", "open");
    HA.loop();   // sends queued values within Particle's 1 publish/s limit
}
```

Call `HA.begin()` in `setup()` to answer `hass-sync`. Each value goes out as private event `ha/<key>` with `{"v":…, "u": unit, "dc": device class,
"sc": state class, "n": name}`. A value is only sent when it changes, plus every 15 min
(`HA.refreshSeconds`). Plain `Particle.publish("ha/key", "42")` works too; the JSON is optional.
`firmware/example/` uses everything above (variable, function, subscribe, ha/ values).

## Install

1. Copy `custom_components/particle` to `/config/custom_components/` (or add the repo to HACS as a
   custom repository) and restart HA.
2. *Settings → Devices & services → Add integration → Particle*.
3. Paste a token: `particle token create --never-expires`. Pick devices and a poll interval.

Updating: copy the folder again and restart HA (a reload doesn't load new code). From this repo:

```sh
tar -C custom_components --exclude=__pycache__ -cf - particle | ssh root@<ha-host> 'tar -xf - -C /homeassistant/custom_components'
ssh root@<ha-host> 'ha core check && ha core restart'
```

## Adding devices

*Settings → Devices & services → Particle → Configure*: tick devices in the list (all the account's
devices, online first) and set the poll interval. The integration reloads by itself. A second Particle
account is *Add entry* on the same page. Function buttons are created disabled (an empty argument can
misfire, e.g. `brightness("")` = 0); enable the ones that make sense.

## Logo

`custom_components/particle/brand/` holds the icon HA shows for the integration (HA 2026.x serves
`brand/icon.png` etc. from custom integrations; Particle isn't in the central brands repo). It is
Particle's mark, rendered from `assets/particle-logo.svg` in Particle's own
[snowflake](https://github.com/particle-iot/snowflake) repo. Re-render with:

```sh
magick -background none -density 1200 particle-logo.svg -resize 236x236 -gravity center -extent 256x256 PNG32:icon.png
magick -background none -density 2400 particle-logo.svg -resize 472x472 -gravity center -extent 512x512 PNG32:icon@2x.png
```

Costs: each variable read is a cloud round trip, which counts as a data operation and uses data on
cellular devices. With many variables on cellular boards, raise the poll interval and push values
with `ha/` events instead. The canary is published from the API, not by a device.

## Tested

2026-11-22 against HA 2026.11.2 (throwaway instance) and the live Particle Cloud, with the example firmware
on an M-SoM: config flow, online sensor, variable → sensor, function → button/action with
return value, `ha/` sensors (unit/device class/state class, restored after restart), reload on
new variables/functions, `publish_event` → device `Particle.subscribe`, `particle_event` on the bus,
event stream health + canary. Lights: Snowflake P2 (Device OS 5.6.0): effect, brightness, colour, off/on
from HA all matched the device's own variables; state arrives on boot and on `hass-sync`. Pattern
select: picking a pattern switches the device and the light's effect follows. Brand icon served by HA. Failure modes (zombie stream, dead socket, long outage → Repairs
issue, revoked token) are covered by the unit tests only.
