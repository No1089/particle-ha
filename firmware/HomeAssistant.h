// HomeAssistant.h: publish values that the Home Assistant "particle" integration
// turns into sensors on its own. Header only; copy it into your project's src/.
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

    // Call once in setup(): when HA's event stream (re)connects it publishes
    // "hass-sync", and every value is sent again so HA catches up at once.
    void begin() { Particle.subscribe("hass-sync", &HomeAssistantClass::onSync, this); }

    void loop() {
        if (!Particle.connected() || millis() - lastSend < 1100) return;
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
