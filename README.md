# Particle for Home Assistant

<img src="custom_components/particle/brand/icon.png" width="64" align="right" alt="">

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=No1089&repository=particle-ha&category=integration)
[![Open your Home Assistant instance and start setting up Particle.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=particle)

A Home Assistant integration for Particle devices (Photon 2, P2, M-SoM, Boron, Argon, …) through the
Particle Cloud. Log in once, pick a device, and its variables, functions and events show up in Home
Assistant. Home Assistant gets its own API user, limited to one product and six permissions; your
login is never stored.

| On the device | In Home Assistant |
|---|---|
| online / offline | `binary_sensor.<device>_online`, live |
| `HA.value(...)` | sensors (temperature, humidity, anything with a unit), live |
| `HA.addLight(...)` | lights: on/off, brightness, colour, effects (plus a Pattern select for HomeKit) |
| `HA.addSwitch(...)` | switches |
| `HA.addBinary(...)` | binary sensors: door, window, motion, leak, … |
| `HA.addNumber(...)` | numbers (sliders/boxes) for setpoints and settings |
| `HA.addEvent(...)` + `HA.event(...)` | event entities: button presses as automation triggers |
| `Particle.variable(…)` | sensors, read every *poll interval* |
| `Particle.function(…)` | action `particle.call_function` (returns the int), and a button (disabled by default) |
| any other `Particle.publish(…)` | `particle_event` on the event bus, for automations |
| `Particle.subscribe(…)` | action `particle.publish_event` sends events to the devices |

`HA.*` comes from `firmware/HomeAssistant.h`, a header-only helper (see [Firmware](#firmware)).

New variables or functions after a firmware update get their entities on their own: the integration
notices the change after the device comes back online and reloads.

## Install

**HACS** (recommended): click *Open in HACS* above, or in HACS add `https://github.com/No1089/particle-ha`
as a custom repository (type *Integration*). Download it, restart Home Assistant, then click *Add
integration* above or go to *Settings → Devices & services → Add integration → Particle*. HACS offers
updates like any other integration.

**Manual**: copy `custom_components/particle` into your Home Assistant `config/custom_components/`
folder and restart.

Needs Home Assistant 2026.11 or newer and a Particle account with a password (SSO-only accounts
can't use the password login).

## Setup and access

The setup asks for your Particle login once and turns it into a narrowly scoped credential for
Home Assistant:

1. **Log in** with your Particle email and password, and the code from your authenticator if your
   account has two-step verification. This creates a **login token** that lives at most an hour.
2. **Pick a device**, then its **product**. Home Assistant's access is scoped to a product, so:
   - a device that's already in a product can stay there (the default), or move to another product
     of the same platform, existing or new: it's removed from the old product and added to the new one;
   - a sandbox (developer) device is added to a product of its platform, existing or new.

   Only products of the device's own platform are offered. If the product has released firmware (a
   default or a group release), the device is marked as a *development device*, so that firmware
   won't replace yours.
3. **API user.** The integration creates an API user on that product, named *Home Assistant
   (your home's name)*, with exactly these scopes:

   | Scope | Used for |
   |---|---|
   | `devices:list` | the product's device list (*Configure → Devices*) |
   | `devices:get` | online state, variable and function names |
   | `devices.variable:get` | reading variables |
   | `devices.function:call` | calling functions (buttons, lights, `particle.call_function`) |
   | `events:get` | the live event stream |
   | `events:send` | `particle.publish_event`, plus the integration's own `hass-ping` / `hass-sync` |

   It can't flash firmware, claim or remove devices, change product settings, or see anything
   outside that product. All calls go to the product endpoints (`/v1/products/<id>/…`).
4. **Done.** The login token is deleted (`DELETE /v1/access_tokens/current`), as it is whenever the
   flow ends early or is cancelled.

**What Home Assistant stores**, in its config entry: the API user's token, its username (needed to
delete it later), and the product's id, name and slug. Your email, password, two-step code and login
token are only held in memory while the setup dialog is open.

Each product is one entry in Home Assistant, with one API user.

## Adding devices

- **A device in a product that's already set up**: *Configure → Devices*, tick it. No login
  needed: the API user can list its own product's devices.
- **A device that isn't in that product yet** (or a sandbox device): *Add entry* runs the setup
  above. Pick the product that's already set up and the device is added to that entry; no second API
  user is created.
- **A device in another product** (a Boron next to a P2, say): *Add entry*. It becomes its own
  entry with its own API user.

## Managing access

Under *Settings → Devices & services → Particle*:

- **Configure → Revoke access**: log in, and the API user is deleted from the product, which ends
  its token at once. The entry and its entities stay, and Home Assistant asks you to re-authenticate.
  This needs your login because Particle doesn't let an API user delete itself or revoke its own token.
- **Re-authenticate** (offered when the token stops working, or after a revoke): log in, and a
  new API user replaces the old one.
- **Reconfigure**: move the entry to another device or product; entity IDs and history are kept,
  and the old API user is deleted.
- **Delete**: removing the integration can't delete the API user (there's no login at that point),
  so Home Assistant shows a notification naming it, with a link to the product's *Team* page. Use
  *Revoke access* first to avoid that.

## Firmware

Everything device-side is in **`firmware/HomeAssistant.h`**, one header with no dependencies: copy it
into your project's `src/`. Declare entities in `setup()`, call `HA.begin()`, and call `HA.loop()` from
`loop()`:

```cpp
#include "HomeAssistant.h"

static const char *const EFFECTS[] = {"solid", "spin", "rainbow"};
static const char *const PRESSES[] = {"press", "double", "hold"};

void onLight(const char *key, const HALight &l) { /* l.on, l.brightness, l.rgb, l.effect */ }
void onSwitch(const char *key, bool on) { /* drive the relay */ }
void onNumber(const char *key, float v) { /* new setting */ }

void setup() {
    HA.addLight("ring", "Ring", EFFECTS, 3, onLight);
    HA.addSwitch("relay", "Relay", false, onSwitch);
    HA.addBinary("door", "Door", "door", false);
    HA.addNumber("setpoint", "Setpoint", 5, 30, 0.5, 21, "°C", onNumber);
    HA.addEvent("button1", "Button 1", PRESSES, 3);
    HA.begin();
}

void loop() {
    HA.setBinary("door", digitalRead(D2));           // report changes
    if (pressed) HA.event("button1", "press");       // momentary events
    HA.value("temperature", 21.4, "°C", "temperature", "measurement");
    HA.loop();                                        // sends, ~1 publish/s
}
```

In Home Assistant these show up on the device as `light.<device>_ring`, `switch.<device>_relay`,
`binary_sensor.<device>_door`, `number.<device>_setpoint`, `event.<device>_button_1` and
`sensor.<device>_temperature`. After a local change (a button toggling the relay), call the matching
`HA.set…()` (or change `HA.light(key)` and call `HA.lightChanged(key)`) so Home Assistant follows.

How it works:

- Each entity publishes its state as `ha/<kind>/<key>` (`ha/<key>` for sensors) when it changes.
  The integration creates the entity on the first publish and remembers it across restarts.
- Commands from Home Assistant arrive through **one** cloud function, `ha`, as `<kind>/<key>=<value>`
  (lights: `light/<key>/<field>=<value>`, field `on`, `bri`, `rgb` or `fx`). The helper checks the
  value, updates the state, calls your handler and publishes the new state. Anything it can't apply
  returns -1, which Home Assistant shows as an error. One function for everything keeps a device
  well inside Particle's limit on cloud functions.
- Publishes are queued and sent at Particle's rate of about one a second, events first. A state is
  only resent when it changes, every 15 minutes (`HA.refreshSeconds`), and when Home Assistant
  publishes `hass-sync` (whenever its event stream reconnects), so it catches up after outages.
- Event entities are announced at boot, so they can be picked as automation triggers before the
  first press.
- Up to 20 entities and 16 sensors per device (`MAX_ENTITIES`, `MAX_VALUES`).

### Apps

Complete apps in `firmware/apps/` (copy `HomeAssistant.h` into the app's `src/`, then
`cd firmware/apps/<name> && particle compile <platform> .`):

| App | What it shows | Builds for |
|---|---|---|
| `button` | A four-button controller with an LED ring: 4 event entities (press, double, hold), 2 lights (a WS2812B ring with 4 effects, and the onboard RGB LED), 2 switches (chime, night mode), 2 binary sensors (door contact, PIR motion), 2 numbers (hold time, ring auto-off), temperature and humidity from an SHT4x | P2 / Photon 2, Argon, Boron |
| `climate` | Temperature, humidity and dew point from an SHT40/41/45 on I2C, with a number for the measuring interval | any |
| `light` | The onboard RGB status LED as a light with 4 effects; no extra hardware | any |
| `basics` | `Particle.variable`, `Particle.function`, `Particle.subscribe` and `ha/` values | any |

(The folder is `apps`, not `examples`: the Particle CLI treats a folder with an `examples/`
subfolder as a library and refuses to compile the projects inside.)

### Without the helper

The protocol is plain events and one function, so any firmware can speak it: publish `ha/<key>`
with a value or `{"v": 21.4, "u": "°C", "dc": "temperature", "sc": "measurement", "n": "Room"}` for
a sensor; `ha/switch/<key>` and `ha/binary/<key>` with `{"on": 0|1, "n": …, "dc": …}`;
`ha/number/<key>` with `{"v", "min", "max", "step", "u"}`; `ha/event/<key>` with `{"t": "press",
"ts": ["press", …]}`; `ha/light/<key>` with `{"on", "bri", "rgb", "fx", "fxs"}`. Add `"cmd": "ha"`
to the commandable ones and implement that function.

A light can also use separate functions instead of `ha`: publish `ha/light` without `cmd`, and Home
Assistant calls `mode("on" | "off" | <effect>)`, `brightness("0".."255")` and `color("rrggbb")` (names
can be changed with `"fn": {"power": …, "fx": …, "bri": …, "rgb": …}`).

With an effect list, a light also gets a **Pattern** select: HomeKit has no light effects, but Home
Assistant's HomeKit bridge shows a select as one switch per option, so patterns can be picked in the
Home app, in scenes and with Siri.

## The event stream

The integration keeps one outgoing Server-Sent Events connection per product
(`/v1/products/<id>/events`), so Home Assistant doesn't need to be reachable from the internet and no
webhook is involved. SSE connections fail in two ways, and both are handled
(`custom_components/particle/stream.py`):

1. **Dead connection** (Wi-Fi drop, NAT timeout, cloud restart). Particle sends a keepalive about
   every 9 s, so 60 s of silence ends the connection. Reconnects back off from 5 s to 5 min, with
   jitter, and go back to fast retries after a connection that stayed healthy.
2. **Zombie stream**: keepalives arrive but events don't. Every 5 minutes the integration publishes
   `hass-ping` with a random value; if it doesn't come back on the stream within 45 s, the
   connection is replaced.

After every reconnect the integration re-reads the devices and publishes `hass-sync`. If the stream
stays down for 15 minutes, a Repairs issue appears, and it clears itself on recovery.
`binary_sensor.particle_cloud_event_stream` shows the health, with attributes `connects`,
`last_probe_ok`, `last_probe_ms` (the canary's round trip) and `last_error`.

## Costs

Each variable read is a cloud round trip; it counts as a data operation and uses data on cellular
devices. With many variables on cellular devices, raise the poll interval (*Configure → Devices*) and
push values as `ha/` events instead. `hass-ping` and `hass-sync` are published from the cloud API,
not by devices; `hass-sync` reaches only devices that subscribe to it.

## Development

- Tests: `pip install pytest-homeassistant-custom-component`, then `pytest` runs the integration in
  a test Home Assistant against a fake Particle Cloud (`tests/test_entities.py`: every entity type,
  the commands sent back, rejected commands, entities surviving a reload), and
  `python tests/check_stream.py` drives the event stream through its failure modes. CI
  (`.github/workflows/validate.yml`) runs both, plus `hassfest` and the HACS validator.
- Releases: bump `version` in `manifest.json`, tag `vX.Y.Z`, publish a GitHub release; HACS offers it
  as an update.
- Copying straight to a Home Assistant OS box during development:

  ```sh
  tar -C custom_components --exclude=__pycache__ -cf - particle | ssh root@<ha-host> 'tar -xf - -C /homeassistant/custom_components'
  ssh root@<ha-host> 'ha core check && ha core restart'
  ```

- The icon in `custom_components/particle/brand/` is Particle's mark, rendered from
  `assets/particle-logo.svg` in Particle's [snowflake](https://github.com/particle-iot/snowflake) repo
  (Home Assistant serves `brand/` images from custom integrations):

  ```sh
  magick -background none -density 1200 particle-logo.svg -resize 236x236 -gravity center -extent 256x256 PNG32:icon.png
  magick -background none -density 2400 particle-logo.svg -resize 472x472 -gravity center -extent 512x512 PNG32:icon@2x.png
  ```

## Tested

On Home Assistant 2026.11.2 against the live Particle Cloud (P2 on Device OS 5.6.0, M-SoM on 6.6.1),
November 2026:

- **Setup and access**: a real login with Reconfigure onto a product API user. With the login
  request stubbed in a test instance: wrong password, wrong and right two-step code; device list; product choices (keep / move / new, only the device's
  platform); adding a device to an already set up product; Revoke (API user deleted, token dead at
  once); re-authenticate (new API user); login token deleted when the flow ends or is cancelled.
  Against the API: an API user can't delete itself or revoke its own token.
- **Runtime**: online sensor, variables, functions (button and action with return value), `ha/`
  sensors with metadata and restore, reload on new variables/functions, `publish_event` reaching a
  device's subscription, `particle_event`, lights and the Pattern select (state matched the device's
  own variables), `hass-sync` on (re)connect, event stream canary.
- **Failure modes** (dead connection, zombie stream, long outage and Repairs issue, rejected token):
  unit tests with a fake client.
- **Entities** (`pytest`, test Home Assistant with a fake cloud): switches, binary sensors, numbers,
  events (announce, then trigger), lights and the Pattern select through the `ha` function, sensors,
  `hass-sync`, a rejected command raising an error, entities re-created after a reload.
- **Firmware**: `apps/light` on an M-SoM: the light announces itself, `ha` commands change the LED
  and publish the new state, `hass-sync` gets an answer, bad commands return -1. All apps build for
  their listed platforms (Device OS 6.3.0); `apps/button` and `apps/climate` haven't run on their
  hardware yet.
- **Not run yet**: actually moving or importing a device into a product, creating a product, the
  development-device flag.

## Licence

MIT, see [LICENSE](LICENSE). The Particle name and logo belong to Particle Industries.
