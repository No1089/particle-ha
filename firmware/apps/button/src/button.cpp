// A four-button controller with an LED ring, as a Home Assistant device: two or more
// of every entity type the integration supports.
//
// Hardware (change the pins below to match yours): a P2 / Photon 2, M-SoM, Argon or
// Boron with
//   - 4 push buttons to GND on D4..D7           -> 4 events (press, double, hold)
//   - a ring of 12 WS2812B LEDs: data on MOSI (P2 / Photon 2, M-SoM) or D3 (Argon, Boron)
//                                               -> light "Ring" with effects
//   - the onboard RGB status LED                -> light "Status"
//   - a piezo buzzer on D2 (optional)           -> switch "Chime": beep on presses
//   -                                           -> switch "Night mode": dims the ring
//   - a door/reed contact to GND on A0          -> binary sensor "Door"
//   - a PIR motion sensor output on A1          -> binary sensor "Motion"
//   -                                           -> number "Hold time" (when a press is a hold)
//   -                                           -> number "Auto off" (ring off after N minutes)
//   - an SHT40/41/45 on I2C (SDA/SCL), optional -> sensors "Temperature", "Humidity"
//   -                                           -> button "Beep": sound the buzzer
//   -                                           -> button "Identify": flash the ring white
//
// In Home Assistant the buttons are event entities: use them as automation triggers
// ("Button 1 · hold"). Everything is declared in setup(); HomeAssistant.h does the rest.
//
// Note: while this runs, the RGB LED no longer shows the connection status.

#include "Particle.h"
#include "neopixel.h"
#include "HomeAssistant.h"

SYSTEM_THREAD(ENABLED);
SerialLogHandler logHandler(LOG_LEVEL_INFO);

// ---- pins -------------------------------------------------------------------------------
static const pin_t BUTTON_PINS[4] = {D4, D5, D6, D7};
static const pin_t BUZZER_PIN = D2;
static const pin_t DOOR_PIN = A0;    // contact to GND: closed = LOW = door shut
static const pin_t MOTION_PIN = A1;  // PIR output, HIGH = motion
static const uint16_t RING_LEDS = 12;
// lib/neopixel is the neopixel library patched for every RTL872x platform (see PATCHED.md).
#if HAL_PLATFORM_RTL872X
static Adafruit_NeoPixel ring(RING_LEDS, SPI, WS2812B);  // P2 / Photon 2, M-SoM: data on MOSI
#else
static Adafruit_NeoPixel ring(RING_LEDS, D3, WS2812B);   // Argon, Boron: data on D3
#endif

// ---- Home Assistant declarations ---------------------------------------------------------
static const char *const RING_EFFECTS[] = {"solid", "spin", "rainbow", "pulse"};
static const char *const STATUS_EFFECTS[] = {"solid", "breathe"};
static const char *const PRESS_TYPES[] = {"press", "double", "hold"};
static const char *const BUTTON_KEYS[4] = {"button1", "button2", "button3", "button4"};
static const char *const BUTTON_NAMES[4] = {"Button 1", "Button 2", "Button 3", "Button 4"};

static uint32_t ringOnSince = 0;  // for Auto off

static void onLight(const char *key, const HALight &l) {
    Log.info("%s: %s bri %u rgb %06lx effect %u", key, l.on ? "on" : "off", l.brightness,
             (unsigned long)l.rgb, l.effect);
    if (!strcmp(key, "ring") && l.on) ringOnSince = millis();
}
static void onSwitch(const char *key, bool on) { Log.info("switch %s: %s", key, on ? "on" : "off"); }
static void onNumber(const char *key, float v) { Log.info("number %s: %.2f", key, v); }

static uint32_t identifyUntil = 0;
static void onButton(const char *key) {
    Log.info("button %s pressed in Home Assistant", key);
    if (!strcmp(key, "beep")) tone(BUZZER_PIN, 2700, 200);
    if (!strcmp(key, "identify")) identifyUntil = millis() + 3000;
}

// ---- SHT4x (temperature + humidity), no library needed -----------------------------------
static bool readSht4x(float &tempC, float &rh) {
    Wire.beginTransmission(0x44);
    Wire.write(0xFD);  // measure, high precision
    if (Wire.endTransmission() != 0) return false;
    delay(10);
    if (Wire.requestFrom(0x44, 6) != 6) return false;
    uint8_t b[6];
    for (int i = 0; i < 6; i++) b[i] = Wire.read();
    tempC = -45 + 175.0f * ((b[0] << 8) | b[1]) / 65535.0f;
    rh = -6 + 125.0f * ((b[3] << 8) | b[4]) / 65535.0f;
    rh = rh < 0 ? 0 : rh > 100 ? 100 : rh;
    return true;
}

// ---- buttons: press / double / hold --------------------------------------------------------
struct Button {
    bool down = false, holdSent = false;
    uint32_t downAt = 0, upAt = 0;
    uint8_t clicks = 0;
};
static Button buttons[4];

static void beep(int ms) {
    if (HA.switchState("chime")) tone(BUZZER_PIN, 2700, ms);
}

static void pollButtons() {
    uint32_t now = millis();
    uint32_t holdMs = (uint32_t)(HA.number("hold_time") * 1000);
    for (int i = 0; i < 4; i++) {
        Button &b = buttons[i];
        bool pressed = digitalRead(BUTTON_PINS[i]) == LOW;
        if (pressed && !b.down && now - b.upAt > 30) {  // press (debounced)
            b.down = true;
            b.downAt = now;
            b.holdSent = false;
        } else if (pressed && b.down && !b.holdSent && now - b.downAt >= holdMs) {
            b.holdSent = true;  // hold fires while still held
            b.clicks = 0;
            HA.event(BUTTON_KEYS[i], "hold");
            beep(150);
        } else if (!pressed && b.down && now - b.downAt > 30) {  // release
            b.down = false;
            b.upAt = now;
            if (!b.holdSent) b.clicks++;
        }
        if (!b.down && b.clicks && now - b.upAt > 350) {  // no second press in time
            HA.event(BUTTON_KEYS[i], b.clicks >= 2 ? "double" : "press");
            beep(b.clicks >= 2 ? 40 : 60);
            b.clicks = 0;
        }
    }
}

// ---- outputs --------------------------------------------------------------------------------
static uint32_t wheel(uint8_t pos) {
    if (pos < 85) return ring.Color(255 - pos * 3, pos * 3, 0);
    if (pos < 170) { pos -= 85; return ring.Color(0, 255 - pos * 3, pos * 3); }
    pos -= 170;
    return ring.Color(pos * 3, 0, 255 - pos * 3);
}

static void drawRing() {
    HALight &l = *HA.light("ring");
    uint32_t t = millis();
    float autoOffMin = HA.number("auto_off");
    if (l.on && autoOffMin > 0 && t - ringOnSince > autoOffMin * 60000) {
        l.on = false;  // a local change: tell Home Assistant
        HA.lightChanged("ring");
    }
    uint8_t level = l.on ? l.brightness : 0;
    if (HA.switchState("night")) level = level / 8;
    uint8_t r = l.rgb >> 16, g = l.rgb >> 8, b = l.rgb;
    if (t < identifyUntil) {  // "Identify": blink white, whatever the light is doing
        bool on = (t / 250) % 2;
        for (uint16_t i = 0; i < RING_LEDS; i++) ring.setPixelColor(i, on ? ring.Color(255, 255, 255) : 0);
        ring.setBrightness(160);
        ring.show();
        return;
    }
    for (uint16_t i = 0; i < RING_LEDS; i++) {
        uint32_t c = ring.Color(r, g, b);
        switch (l.effect) {
            case 1: {  // spin: one bright pixel with a short tail
                int head = (t / 80) % RING_LEDS;
                int d = (head - (int)i + RING_LEDS) % RING_LEDS;
                uint8_t f = d == 0 ? 255 : d == 1 ? 90 : d == 2 ? 30 : 0;
                c = ring.Color(r * f / 255, g * f / 255, b * f / 255);
                break;
            }
            case 2: c = wheel((i * 256 / RING_LEDS + t / 10) & 0xFF); break;  // rainbow
            case 3: {  // pulse
                uint32_t ph = t % 2000, tri = ph < 1000 ? ph : 2000 - ph;
                uint8_t f = 40 + tri * 215 / 1000;
                c = ring.Color(r * f / 255, g * f / 255, b * f / 255);
                break;
            }
        }
        ring.setPixelColor(i, c);
    }
    ring.setBrightness(level);
    ring.show();
}

static void drawStatus() {
    const HALight &l = *HA.light("status");
    uint8_t level = l.on ? l.brightness : 0;
    if (l.on && l.effect == 1) {  // breathe
        uint32_t ph = millis() % 3000, tri = ph < 1500 ? ph : 3000 - ph;
        level = l.brightness * (20 + tri * 80 / 1500) / 100;
    }
    RGB.color(l.rgb >> 16, (l.rgb >> 8) & 0xFF, l.rgb & 0xFF);
    RGB.brightness(level);
}

// ---- setup / loop -----------------------------------------------------------------------
void setup() {
    for (pin_t p : BUTTON_PINS) pinMode(p, INPUT_PULLUP);
    pinMode(DOOR_PIN, INPUT_PULLUP);
    pinMode(MOTION_PIN, INPUT_PULLDOWN);
    pinMode(BUZZER_PIN, OUTPUT);
    Wire.begin();
    ring.begin();
    RGB.control(true);

    HALight *r = HA.addLight("ring", "Ring", RING_EFFECTS, 4, onLight);
    r->rgb = 0xFF8C32;
    HALight *s = HA.addLight("status", "Status", STATUS_EFFECTS, 2, onLight);
    s->rgb = 0x0040FF;
    s->brightness = 40;
    HA.addSwitch("chime", "Chime", true, onSwitch);
    HA.addSwitch("night", "Night mode", false, onSwitch);
    HA.addBinary("door", "Door", "door", false);
    HA.addBinary("motion", "Motion", "motion", false);
    HA.addNumber("hold_time", "Hold time", 0.3, 3, 0.1, 0.8, "s", onNumber);
    HA.addNumber("auto_off", "Auto off", 0, 120, 1, 0, "min", onNumber);
    for (int i = 0; i < 4; i++) HA.addEvent(BUTTON_KEYS[i], BUTTON_NAMES[i], PRESS_TYPES, 3);
    HA.addButton("beep", "Beep", onButton);
    HA.addButton("identify", "Identify", onButton);
    HA.begin();
    ringOnSince = millis();
}

void loop() {
    static uint32_t lastFrame = 0, lastClimate = 0;
    pollButtons();
    HA.setBinary("door", digitalRead(DOOR_PIN) == HIGH);  // contact open = door open
    HA.setBinary("motion", digitalRead(MOTION_PIN) == HIGH);
    if (millis() - lastFrame >= 20) {
        lastFrame = millis();
        drawRing();
        drawStatus();
    }
    if (millis() - lastClimate >= 5 * 60000 || lastClimate == 0) {  // one ha/batch per 5 min
        lastClimate = millis();
        float t, rh;
        if (readSht4x(t, rh)) {
            HA.value("temperature", t, "°C", "temperature", "measurement", 1);
            HA.value("humidity", rh, "%", "humidity", "measurement", 0);
        }
    }
    HA.loop();
}
