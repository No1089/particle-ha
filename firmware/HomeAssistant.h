// HomeAssistant.h: the device side of the Home Assistant "particle" integration
// (https://github.com/No1089/particle-ha). Header only; copy it into your project's src/.
//
// Sensors: HA.value(...) publishes values that become HA sensors on their own.
// Lights:  HA.light(...) makes the device an HA light (see the light section below).
//
//   #include "HomeAssistant.h"
//   HA.begin();                                             // in setup()
//   HA.value("temperature", 21.4, "°C", "temperature");   // -> sensor "Temperature"
//   HA.value("door", "open");                               // -> text sensor
//   HA.value("battery", 87, "%", "battery");
//
// Each call publishes the private event `ha/<key>` with
//   {"v": value, "u": unit, "dc": device_class, "sc": state_class, "n": name}
// (empty fields are left out). device_class / state_class are HA's names, e.g.
// "temperature", "humidity", "battery", "power", "energy" / "measurement", "total_increasing".
//
// Particle limits publishes to about 1 per second. value() queues the newest value
// per key and loop() sends the queue at that pace, so calling value() often is fine:
// call HA.loop() from loop(). A value is resent only when it changed, or every
// `refreshSeconds` (default 15 min) so HA catches up after its own restarts.
#pragma once
#include "Particle.h"

// State of a light, as Home Assistant sees it.
struct HALight {
    bool on = true;
    uint8_t brightness = 128;  // 0-255
    uint32_t rgb = 0xFFFFFF;   // 0xRRGGBB
    uint8_t effect = 0;        // index into the effect list
};
// Called after Home Assistant changed the light: apply `l` to your hardware.
typedef void (*HALightHandler)(const HALight &l);

class HomeAssistantClass {
public:
    static const int MAX_KEYS = 16;

    void value(const char *key, double v, const char *unit = nullptr,
               const char *deviceClass = nullptr, const char *stateClass = nullptr, int decimals = 2) {
        char num[32];
        snprintf(num, sizeof(num), "%.*f", decimals, v);
        set(key, num, unit, deviceClass, stateClass, false);
    }
    void value(const char *key, int v, const char *unit = nullptr,
               const char *deviceClass = nullptr, const char *stateClass = nullptr) {
        char num[16];
        snprintf(num, sizeof(num), "%d", v);
        set(key, num, unit, deviceClass, stateClass, false);
    }
    void value(const char *key, const char *text, const char *deviceClass = nullptr) {
        set(key, text, nullptr, deviceClass, nullptr, true);
    }

    // Optional friendly name shown in HA (default: key with "_" -> " ").
    void name(const char *key, const char *friendly) {
        Slot *s = slot(key);
        if (s) { strlcpy(s->name, friendly, sizeof(s->name)); s->dirty = true; }
    }

    uint32_t refreshSeconds = 15 * 60;

    // ---- light ---------------------------------------------------------------------
    // Makes the device a light in HA: on/off, brightness, colour, and effects (HA's
    // effect list, plus a "Pattern" select that HomeKit can use). Registers three cloud
    // functions, which the integration calls:
    //   mode("on" | "off" | <effect name>), brightness("0".."255"), color("rrggbb")
    // and publishes the state as `ha/light` whenever it changes. `effects` must stay
    // valid (a static array); pass nullptr / 0 for a light without effects. Call before
    // HA.begin(). After a local change (a button, say), edit HA.state() and call
    // HA.lightChanged() so HA follows.
    void light(const char *const *effects, uint8_t effectCount, HALightHandler onChange) {
        lightEffects = effects;
        lightEffectCount = effectCount;
        lightHandler = onChange;
        hasLight = true;
        lightDirty = true;
        Particle.function("mode", &HomeAssistantClass::fnMode, this);
        Particle.function("brightness", &HomeAssistantClass::fnBrightness, this);
        Particle.function("color", &HomeAssistantClass::fnColor, this);
    }
    HALight &state() { return lightState; }
    void lightChanged() { lightDirty = true; }

    // Call once in setup(): when HA's event stream (re)connects it publishes
    // "hass-sync", and every value is sent again so HA catches up at once.
    void begin() { Particle.subscribe("hass-sync", &HomeAssistantClass::onSync, this); }

    void loop() {
        if (!Particle.connected() || millis() - lastSend < 1100) return;
        if (hasLight && (lightDirty || lightSentAt == 0 || millis() - lightSentAt > refreshSeconds * 1000UL)) {
            publishLight();
            return;
        }
        Slot *pick = nullptr;
        for (int i = 0; i < count; i++) {
            Slot &s = slots[i];
            bool stale = s.sentAt == 0 || millis() - s.sentAt > refreshSeconds * 1000UL;
            if (s.dirty || stale) { pick = &s; break; }
        }
        if (!pick) return;
        char json[256];
        JSONBufferWriter w(json, sizeof(json) - 1);
        w.beginObject();
        if (pick->isText) w.name("v").value(pick->val);
        else w.name("v").value(atof(pick->val), decimalsOf(pick->val));
        if (pick->unit[0]) w.name("u").value(pick->unit);
        if (pick->dc[0]) w.name("dc").value(pick->dc);
        if (pick->sc[0]) w.name("sc").value(pick->sc);
        if (pick->name[0]) w.name("n").value(pick->name);
        w.endObject();
        json[std::min(w.dataSize(), sizeof(json) - 1)] = 0;
        char ev[64];
        snprintf(ev, sizeof(ev), "ha/%s", pick->key);
        lastSend = millis();
        if (Particle.publish(ev, json, PRIVATE)) {
            pick->dirty = false;
            pick->sentAt = millis() ? millis() : 1;
        }
    }

private:
    struct Slot {
        char key[32], val[64], unit[12], dc[24], sc[20], name[32];
        bool isText, dirty;
        uint32_t sentAt;
    };
    Slot slots[MAX_KEYS] = {};
    int count = 0;
    uint32_t lastSend = 0;

    void onSync(const char *, const char *) {
        for (int i = 0; i < count; i++) slots[i].dirty = true;
        lightDirty = true;
    }

    // light
    bool hasLight = false, lightDirty = false;
    uint32_t lightSentAt = 0;
    HALight lightState;
    const char *const *lightEffects = nullptr;
    uint8_t lightEffectCount = 0;
    HALightHandler lightHandler = nullptr;

    void lightApply() {
        lightDirty = true;
        if (lightHandler) lightHandler(lightState);
    }

    int fnMode(String arg) {
        arg.trim();
        arg.toLowerCase();
        if (arg == "off") { lightState.on = false; lightApply(); return 0; }
        if (arg == "on") { lightState.on = true; lightApply(); return 1; }
        for (uint8_t i = 0; i < lightEffectCount; i++) {
            if (arg.equalsIgnoreCase(lightEffects[i])) {
                lightState.effect = i;
                lightState.on = true;
                lightApply();
                return i;
            }
        }
        return -1;  // HA shows a negative return value as an error
    }

    int fnBrightness(String arg) {
        arg.trim();
        if (arg.length() == 0 || arg.length() > 3) return -1;
        for (unsigned i = 0; i < arg.length(); i++)
            if (!isdigit((unsigned char)arg.charAt(i))) return -1;
        int v = arg.toInt();
        if (v > 255) return -1;
        lightState.brightness = v;
        lightApply();
        return v;
    }

    int fnColor(String arg) {
        arg.trim();
        if (arg.startsWith("#")) arg = arg.substring(1);
        if (arg.length() != 6) return -1;
        char *end = nullptr;
        uint32_t c = strtoul(arg.c_str(), &end, 16);
        if (!end || *end) return -1;
        lightState.rgb = c;
        lightState.on = true;
        lightApply();
        return 0;
    }

    void publishLight() {
        char json[512];
        JSONBufferWriter w(json, sizeof(json) - 1);
        char hex[7];
        snprintf(hex, sizeof(hex), "%06lx", (unsigned long)(lightState.rgb & 0xFFFFFF));
        w.beginObject();
        w.name("on").value(lightState.on ? 1 : 0);
        w.name("bri").value((int)lightState.brightness);
        w.name("rgb").value(hex);
        if (lightEffectCount) {
            w.name("fx").value(lightEffects[lightState.effect < lightEffectCount ? lightState.effect : 0]);
            w.name("fxs").beginArray();
            for (uint8_t i = 0; i < lightEffectCount; i++) w.value(lightEffects[i]);
            w.endArray();
        }
        w.endObject();
        json[std::min(w.dataSize(), sizeof(json) - 1)] = 0;
        lastSend = millis();
        if (Particle.publish("ha/light", json, PRIVATE)) {
            lightDirty = false;
            lightSentAt = millis() ? millis() : 1;
        }
    }

    static int decimalsOf(const char *s) {
        const char *dot = strchr(s, '.');
        return dot ? (int)strlen(dot + 1) : 0;
    }

    Slot *slot(const char *key) {
        for (int i = 0; i < count; i++)
            if (!strcmp(slots[i].key, key)) return &slots[i];
        if (count >= MAX_KEYS) return nullptr;
        Slot &s = slots[count++];
        strlcpy(s.key, key, sizeof(s.key));
        return &s;
    }

    void set(const char *key, const char *val, const char *unit, const char *dc,
             const char *sc, bool isText) {
        Slot *s = slot(key);
        if (!s) return;
        if (strcmp(s->val, val) != 0 || s->sentAt == 0) s->dirty = true;
        strlcpy(s->val, val, sizeof(s->val));
        strlcpy(s->unit, unit ? unit : "", sizeof(s->unit));
        strlcpy(s->dc, dc ? dc : "", sizeof(s->dc));
        strlcpy(s->sc, sc ? sc : "", sizeof(s->sc));
        s->isText = isText;
    }
};

inline HomeAssistantClass HA;
