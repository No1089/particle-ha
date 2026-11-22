// A Home Assistant light on any Particle device, using its onboard RGB status LED.
//
// Flash it, add the device in Home Assistant (Particle integration), and it shows up
// as a light: on/off, brightness, colour, and the effects below. The effects also
// appear as a "Pattern" select, which HomeKit (via HA's HomeKit bridge) can use.
//
// Everything Home Assistant needs is in HomeAssistant.h: HA.light() registers the
// cloud functions mode / brightness / color and publishes the state as `ha/light`.
// Swap applyLight()/animate() for your own LEDs (a NeoPixel strip, a PWM channel)
// and the Home Assistant side stays the same.
//
// Note: while this runs, the RGB LED no longer shows the connection status.

#include "Particle.h"
#include "HomeAssistant.h"

SYSTEM_THREAD(ENABLED);
SerialLogHandler logHandler(LOG_LEVEL_INFO);

// Effect names as shown in Home Assistant. Index 0 is the default.
static const char *const EFFECTS[] = {"solid", "breathe", "rainbow", "blink"};
enum { SOLID, BREATHE, RAINBOW, BLINK };

static void applyLight(const HALight &l) {
    Log.info("light: %s, brightness %u, colour %06lx, effect %s", l.on ? "on" : "off",
             l.brightness, (unsigned long)l.rgb, EFFECTS[l.effect]);
    // animate() draws every frame; nothing else to do here. A light that only changes
    // on command (no effects) would set its outputs right here instead.
}

static void colourWheel(uint8_t pos, uint8_t &r, uint8_t &g, uint8_t &b) {
    if (pos < 85) { r = 255 - pos * 3; g = pos * 3; b = 0; }
    else if (pos < 170) { pos -= 85; r = 0; g = 255 - pos * 3; b = pos * 3; }
    else { pos -= 170; r = pos * 3; g = 0; b = 255 - pos * 3; }
}

static void animate() {
    const HALight &l = HA.state();
    uint8_t r = l.rgb >> 16, g = l.rgb >> 8, b = l.rgb, level = l.brightness;
    uint32_t t = millis();
    if (!l.on) {
        level = 0;
    } else if (l.effect == BREATHE) {
        // triangle wave, 3 s period, never fully dark
        uint32_t ph = t % 3000;
        uint32_t tri = ph < 1500 ? ph : 3000 - ph;  // 0..1500
        level = l.brightness * (20 + tri * 80 / 1500) / 100;
    } else if (l.effect == RAINBOW) {
        colourWheel((t / 20) & 0xFF, r, g, b);  // ignores the colour, keeps the brightness
    } else if (l.effect == BLINK) {
        if ((t / 500) % 2) level = 0;
    }
    RGB.color(r, g, b);
    RGB.brightness(level);
}

void setup() {
    RGB.control(true);
    HA.light(EFFECTS, sizeof(EFFECTS) / sizeof(EFFECTS[0]), applyLight);
    HA.state().rgb = 0xFF8C32;  // warm white to start with
    HA.begin();                 // answer hass-sync (resend the state when HA reconnects)
}

void loop() {
    static uint32_t last = 0;
    if (millis() - last >= 20) {  // ~50 fps
        last = millis();
        animate();
    }
    HA.loop();  // publishes ha/light after changes, within Particle's 1/s publish limit
}
