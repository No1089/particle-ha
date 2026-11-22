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
| `Particle.variable("x", …)` | `sensor.<device>_x`, read every *poll interval* |
| `Particle.function("f", …)` | action `particle.call_function` (any argument, returns the int), and a button per function (disabled by default) |
| `Particle.publish("ha/<key>", …)` | `sensor.<device>_<key>`, created on first publish, live, kept over restarts |
| `Particle.publish("ha/light", {json})` | `light.<device>` (on/off, brightness, colour, effects) and `select.<device>_pattern` |
| any other `Particle.publish(…)` | event `particle_event` (`device_id`, `event`, `data`, `published_at`) for automations |
| `Particle.subscribe(…)` | action `particle.publish_event` sends events to the devices |

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

What a device needs to do for each kind of entity:

| In Home Assistant | Firmware |
|---|---|
| online / offline | nothing |
| a sensor, read every poll interval | `Particle.variable(...)` |
| an action (and a button) | `Particle.function(...)`, returning an int (negative = error) |
| a live sensor | publish `ha/<key>`: `HA.value(...)` |
| a light | `HA.light(...)`: three functions plus `ha/light` state, all handled by the helper |
| triggers for automations | any other `Particle.publish(...)` arrives as `particle_event` |
| commands from Home Assistant | `Particle.subscribe(...)`, fed by `particle.publish_event` |

Everything on this side is in **`firmware/HomeAssistant.h`**, a header-only helper: copy it into your
project's `src/`. Examples, each a complete app (`particle compile <platform> firmware/examples/<name>/src`
after copying the header into that `src/`):

- `firmware/examples/light`: the device's onboard RGB LED as a Home Assistant light, with four
  effects (solid, breathe, rainbow, blink). No extra hardware; builds for P2/Photon 2, M-SoM, Boron, Argon.
- `firmware/examples/basics`: a variable, a function, a subscription and `ha/` values.

### Values: `ha/<key>`

Any event named `ha/<key>` becomes a sensor. The data can be a plain value (`"42"`, `"open"`) or JSON
with metadata: `{"v": 21.4, "u": "°C", "dc": "temperature", "sc": "measurement", "n": "Room"}`
(value, unit, device class, state class, name).

`firmware/HomeAssistant.h` (header only; copy it into your project's `src/`) does this for you:

```cpp
#include "HomeAssistant.h"

void setup() {
    HA.begin();   // answer hass-sync (see below)
}

void loop() {
    HA.value("temperature", 21.4, "°C", "temperature", "measurement");
    HA.value("door", "open");
    HA.loop();    // sends queued values within Particle's 1 publish/s limit
}
```

A value is sent when it changes, and every 15 minutes (`HA.refreshSeconds`).

### Lights: `ha/light`

With the helper, a light is a few lines (`firmware/examples/light`):

```cpp
static const char *const EFFECTS[] = {"solid", "breathe", "rainbow", "blink"};

void applyLight(const HALight &l) {   // called after Home Assistant changes it
    // l.on, l.brightness (0-255), l.rgb (0xRRGGBB), l.effect (index into EFFECTS)
}

void setup() {
    HA.light(EFFECTS, 4, applyLight);   // registers mode / brightness / color
    HA.begin();
}

void loop() {
    HA.loop();                          // publishes the state after every change
}
```

After a local change (a button on the device), update `HA.state()` and call `HA.lightChanged()`.

Under the hood, a device that publishes `ha/light` (or `ha/light/<name>` for several) becomes a light:

```json
{"on": 1, "bri": 128, "rgb": "ff6a1e", "fx": "rainbow", "fxs": ["warm", "rainbow", "solid"]}
```

Everything but `on` is optional (`rgb` gives a colour picker, `fxs` an effect list). Home Assistant
drives it through cloud functions: `mode("on" | "off" | <effect>)`, `brightness("0".."255")` and
`color("rrggbb")`. Other names can be given in the payload, e.g.
`"fn": {"power": "power", "fx": "effect", "bri": "level", "rgb": "rgb"}`. A negative return value
shows as an error. When an effect and a colour arrive together, the effect is set first, so a device
can use the colour to tint the effect. Publish the state on every change, local ones included
(buttons, touch), so Home Assistant follows.

With an effect list, the device also gets a **Pattern** select. HomeKit has no light effects, but Home
Assistant's HomeKit bridge shows a select as one switch per option, so with the select on the bridge,
patterns can be picked in the Home app, in scenes and with Siri.

### Resync: `hass-sync`

Whenever the integration's event stream (re)connects, it publishes `hass-sync` into the product.
Devices that subscribe to it (`HA.begin()` does) send their state again at once, so nothing stays
stale after an outage.

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

- `tests/test_stream.py` drives the event stream through each failure mode with a fake client:
  `python tests/test_stream.py` in an environment with `homeassistant` installed. CI
  (`.github/workflows/validate.yml`) runs it with `hassfest` and the HACS validator.
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
- **Firmware**: `examples/light` on an M-SoM: `mode` / `brightness` / `color` change the LED and
  each change publishes `ha/light` within a second; unknown effects and out-of-range values return -1.
  Builds for P2, M-SoM, Boron and Argon (Device OS 6.3.0).
- **Not run yet**: actually moving or importing a device into a product, creating a product, the
  development-device flag.

## Licence

MIT, see [LICENSE](LICENSE). The Particle name and logo belong to Particle Industries.
