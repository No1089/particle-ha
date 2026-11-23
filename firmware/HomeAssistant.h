// HomeAssistant.h: the device side of the Home Assistant "particle" integration
// (https://github.com/No1089/particle-ha). Header only; copy it into your project's src/.
//
//   #include "HomeAssistant.h"
//
//   void setup() {
//       HA.addLight("ring", "Ring", EFFECTS, 4, onLight);        // light.<device>_ring
//       HA.addSwitch("chime", "Chime", true, onSwitch);           // switch.<device>_chime
//       HA.addBinary("door", "Door", "door", false);              // binary_sensor.<device>_door
//       HA.addNumber("hold", "Hold time", 0.3, 3, 0.1, 1, "s", onNumber);  // number.<device>_hold_time
//       HA.addEvent("button1", "Button 1", PRESS_TYPES, 3);       // event.<device>_button_1
//       HA.addButton("restart", "Restart", onButton);             // button.<device>_restart
//       HA.begin();
//   }
//   void loop() {
//       HA.value("temperature", 21.4, "°C", "temperature", "measurement");  // sensor
//       HA.setBinary("door", digitalRead(D2));                    // state changes
//       HA.event("button1", "press");                             // momentary events
//       HA.loop();                                                // sends, 1 publish/s
//   }
//
// How it maps (the integration creates the entities when they first publish):
//   ha/<key>            sensor          HA.value()
//   ha/light/<key>      light           HA.addLight()   state: on, bri, rgb, fx, fxs
//   ha/switch/<key>     switch          HA.addSwitch()
//   ha/binary/<key>     binary sensor   HA.addBinary()
//   ha/number/<key>     number          HA.addNumber()
//   ha/event/<key>      event           HA.addEvent() + HA.event()
//   ha/button/<key>     button          HA.addButton()
//
// Commands from Home Assistant all arrive through ONE cloud function, "ha", with the
// argument "<kind>/<key>=<value>" (lights: "light/<key>/<field>=<value>", field is
// on/bri/rgb/fx). The helper parses it, updates the state, calls your handler and
// publishes the new state. One function keeps you well inside Particle's limit on
// the number of functions per device.
//
// Particle allows about one publish per second. Everything is queued and sent at that
// pace (events first), a state is sent again only when it changed, and everything is
// resent every `refreshSeconds` and when Home Assistant asks (it publishes "hass-sync"
// whenever its event stream connects), so HA always catches up.
#pragma once
#include "Particle.h"

// State of a light.
struct HALight {
    bool on = true;
    uint8_t brightness = 128;  // 0-255
    uint32_t rgb = 0xFFFFFF;   // 0xRRGGBB
    uint8_t effect = 0;        // index into the effect list
};
// Handlers: called after Home Assistant changed something; apply it to the hardware.
typedef void (*HALightHandler)(const char *key, const HALight &light);
typedef void (*HASwitchHandler)(const char *key, bool on);
typedef void (*HANumberHandler)(const char *key, float value);
typedef void (*HAButtonHandler)(const char *key);

class HomeAssistantClass {
public:
    static const int MAX_VALUES = 16;    // ha/<key> sensors
    static const int MAX_ENTITIES = 20;  // lights, switches, binary sensors, numbers, events, buttons
    uint32_t refreshSeconds = 15 * 60;

    // ---- declare (in setup(), before begin()) -----------------------------------------
    // Keys: letters, digits and "_"; they become entity IDs. Names are shown in HA.
    // Name, unit, device class and list pointers must stay valid (string literals).

    HALight *addLight(const char *key, const char *name, const char *const *effects,
                      uint8_t effectCount, HALightHandler onChange) {
        Entity *e = add(LIGHT, key, name);
        if (!e) return nullptr;
        e->list = effects;
        e->listCount = effectCount;
        e->cb = (void *)onChange;
        return &e->light;
    }
    void addSwitch(const char *key, const char *name, bool initial, HASwitchHandler onChange) {
        Entity *e = add(SWITCH, key, name);
        if (!e) return;
        e->on = initial;
        e->cb = (void *)onChange;
    }
    // deviceClass: HA's binary sensor class, e.g. "door", "window", "motion", "moisture", "problem".
    void addBinary(const char *key, const char *name, const char *deviceClass, bool initial) {
        Entity *e = add(BINARY, key, name);
        if (!e) return;
        e->dc = deviceClass;
        e->on = initial;
    }
    void addNumber(const char *key, const char *name, float min, float max, float step, float initial,
                   const char *unit, HANumberHandler onChange) {
        Entity *e = add(NUMBER, key, name);
        if (!e) return;
        e->min = min;
        e->max = max;
        e->step = step;
        e->num = initial;
        e->unit = unit;
        e->cb = (void *)onChange;
    }
    // types: the event types HA offers as triggers, e.g. {"press", "double", "hold"}.
    void addEvent(const char *key, const char *name, const char *const *types, uint8_t typeCount) {
        Entity *e = add(EVENT, key, name);
        if (!e) return;
        e->list = types;
        e->listCount = typeCount;
    }

    // A button in Home Assistant; onPress runs when it's pressed there.
    void addButton(const char *key, const char *name, HAButtonHandler onPress) {
        Entity *e = add(BUTTON, key, name);
        if (!e) return;
        e->cb = (void *)onPress;
    }

    // ---- update from the app ------------------------------------------------------------
    void setSwitch(const char *key, bool on) { setOn(SWITCH, key, on); }
    void setBinary(const char *key, bool on) { setOn(BINARY, key, on); }
    void setNumber(const char *key, float v) {
        Entity *e = find(NUMBER, key);
        if (e && e->num != v) { e->num = v; e->dirty = true; }
    }
    bool switchState(const char *key) { Entity *e = find(SWITCH, key); return e && e->on; }
    float number(const char *key) { Entity *e = find(NUMBER, key); return e ? e->num : 0; }
    HALight *light(const char *key) { Entity *e = find(LIGHT, key); return e ? &e->light : nullptr; }
    // After changing a light locally (via light(key)), so HA follows.
    void lightChanged(const char *key) { Entity *e = find(LIGHT, key); if (e) e->dirty = true; }
    // A momentary event, e.g. event("button1", "press"). Queued, sent before states.
    void event(const char *key, const char *type) {
        Entity *e = find(EVENT, key);
        if (!e || queued >= EVENT_QUEUE) return;
        eventQueue[(eventHead + queued++) % EVENT_QUEUE] = {e, type};
    }

    // ---- sensors: ha/<key> ----------------------------------------------------------------
    void value(const char *key, double v, const char *unit = nullptr,
               const char *deviceClass = nullptr, const char *stateClass = nullptr, int decimals = 2) {
        char num[32];
        snprintf(num, sizeof(num), "%.*f", decimals, v);
        setValue(key, num, unit, deviceClass, stateClass, false);
    }
    void value(const char *key, int v, const char *unit = nullptr,
               const char *deviceClass = nullptr, const char *stateClass = nullptr) {
        char num[16];
        snprintf(num, sizeof(num), "%d", v);
        setValue(key, num, unit, deviceClass, stateClass, false);
    }
    void value(const char *key, const char *text, const char *deviceClass = nullptr) {
        setValue(key, text, nullptr, deviceClass, nullptr, true);
    }
    // Optional friendly name for a sensor (default: key with "_" -> " ").
    void name(const char *key, const char *friendly) {
        Slot *s = slot(key);
        if (s) { strlcpy(s->name, friendly, sizeof(s->name)); s->dirty = true; }
    }

    // ---- run ----------------------------------------------------------------------------------
    void begin() {
        Particle.subscribe("hass-sync", &HomeAssistantClass::onSync, this);
        for (int i = 0; i < entityCount; i++) {
            Kind k = entities[i].kind;
            if (k == LIGHT || k == SWITCH || k == NUMBER || k == BUTTON) {
                Particle.function("ha", &HomeAssistantClass::onCommand, this);
                break;
            }
        }
    }

    void loop() {
        if (!Particle.connected() || millis() - lastSend < 1050) return;
        if (queued) {  // events first: they are what someone is waiting for
            QueuedEvent q = eventQueue[eventHead];
            if (publishEntity(*q.e, q.type)) {
                eventHead = (eventHead + 1) % EVENT_QUEUE;
                queued--;
            }
            return;
        }
        for (int i = 0; i < entityCount; i++) {
            Entity &e = entities[i];
            if (e.dirty || e.sentAt == 0 || millis() - e.sentAt > refreshSeconds * 1000UL) {
                publishEntity(e, nullptr);
                return;
            }
        }
        for (int i = 0; i < valueCount; i++) {
            Slot &s = values[i];
            if (s.dirty || s.sentAt == 0 || millis() - s.sentAt > refreshSeconds * 1000UL) {
                publishValue(s);
                return;
            }
        }
    }

private:
    enum Kind : uint8_t { LIGHT, SWITCH, BINARY, NUMBER, EVENT, BUTTON };
    static const char *kindName(Kind k) {
        static const char *const names[] = {"light", "switch", "binary", "number", "event", "button"};
        return names[k];
    }

    struct Entity {
        Kind kind;
        char key[24];
        const char *name;
        bool dirty;
        uint32_t sentAt;
        bool on;
        float num, min, max, step;
        const char *unit, *dc;
        HALight light;
        const char *const *list;
        uint8_t listCount;
        void *cb;
    };
    Entity entities[MAX_ENTITIES] = {};
    int entityCount = 0;

    struct QueuedEvent { Entity *e; const char *type; };
    static const int EVENT_QUEUE = 8;
    QueuedEvent eventQueue[EVENT_QUEUE] = {};
    int eventHead = 0, queued = 0;

    struct Slot {
        char key[32], val[64], unit[12], dc[24], sc[20], name[32];
        bool isText, dirty;
        uint32_t sentAt;
    };
    Slot values[MAX_VALUES] = {};
    int valueCount = 0;
    uint32_t lastSend = 0;

    Entity *add(Kind kind, const char *key, const char *name) {
        if (entityCount >= MAX_ENTITIES || find(kind, key)) return nullptr;
        Entity &e = entities[entityCount++];
        e.kind = kind;
        strlcpy(e.key, key, sizeof(e.key));
        e.name = name;
        e.dirty = true;
        return &e;
    }
    Entity *find(Kind kind, const char *key) {
        for (int i = 0; i < entityCount; i++)
            if (entities[i].kind == kind && !strcmp(entities[i].key, key)) return &entities[i];
        return nullptr;
    }
    void setOn(Kind kind, const char *key, bool on) {
        Entity *e = find(kind, key);
        if (e && e->on != on) { e->on = on; e->dirty = true; }
    }

    void onSync(const char *, const char *) {
        for (int i = 0; i < entityCount; i++) entities[i].dirty = true;
        for (int i = 0; i < valueCount; i++) values[i].dirty = true;
    }

    // "ha" cloud function: "<kind>/<key>=<value>", lights "light/<key>/<field>=<value>".
    // Returns >= 0 on success, -1 for anything it can't apply (HA shows that as an error).
    int onCommand(String arg) {
        int eq = arg.indexOf('=');
        if (eq < 0) return -1;
        String path = arg.substring(0, eq), val = arg.substring(eq + 1);
        val.trim();
        int s1 = path.indexOf('/');
        if (s1 < 0) return -1;
        String kind = path.substring(0, s1), rest = path.substring(s1 + 1), field;
        int s2 = rest.indexOf('/');
        if (s2 >= 0) { field = rest.substring(s2 + 1); rest = rest.substring(0, s2); }

        if (kind == "switch") {
            Entity *e = find(SWITCH, rest.c_str());
            int on = parseBool(val);
            if (!e || on < 0) return -1;
            e->on = on;
            e->dirty = true;
            if (e->cb) ((HASwitchHandler)e->cb)(e->key, e->on);
            return on;
        }
        if (kind == "button") {
            Entity *e = find(BUTTON, rest.c_str());
            if (!e || val != "press") return -1;
            if (e->cb) ((HAButtonHandler)e->cb)(e->key);
            return 0;
        }
        if (kind == "number") {
            Entity *e = find(NUMBER, rest.c_str());
            if (!e || !isNumber(val)) return -1;
            float v = val.toFloat();
            if (v < e->min || v > e->max) return -1;
            e->num = v;
            e->dirty = true;
            if (e->cb) ((HANumberHandler)e->cb)(e->key, v);
            return 0;
        }
        if (kind == "light") {
            Entity *e = find(LIGHT, rest.c_str());
            if (!e) return -1;
            HALight &l = e->light;
            if (field == "on") {
                int on = parseBool(val);
                if (on < 0) return -1;
                l.on = on;
            } else if (field == "bri") {
                if (!isNumber(val) || val.toInt() < 0 || val.toInt() > 255) return -1;
                l.brightness = val.toInt();
            } else if (field == "rgb") {
                if (val.startsWith("#")) val = val.substring(1);
                char *end = nullptr;
                uint32_t c = strtoul(val.c_str(), &end, 16);
                if (val.length() != 6 || !end || *end) return -1;
                l.rgb = c;
                l.on = true;
            } else if (field == "fx") {
                int found = -1;
                for (uint8_t i = 0; i < e->listCount; i++)
                    if (val.equalsIgnoreCase(e->list[i])) found = i;
                if (found < 0) return -1;
                l.effect = found;
                l.on = true;
            } else {
                return -1;
            }
            e->dirty = true;
            if (e->cb) ((HALightHandler)e->cb)(e->key, l);
            return 0;
        }
        return -1;
    }

    static int parseBool(const String &v) {
        if (v == "1" || v.equalsIgnoreCase("on") || v.equalsIgnoreCase("true")) return 1;
        if (v == "0" || v.equalsIgnoreCase("off") || v.equalsIgnoreCase("false")) return 0;
        return -1;
    }
    static bool isNumber(const String &v) {
        if (v.length() == 0) return false;
        for (unsigned i = 0; i < v.length(); i++) {
            char c = v.charAt(i);
            if (!isdigit((unsigned char)c) && c != '.' && !(i == 0 && c == '-')) return false;
        }
        return true;
    }

    // Publishes the entity's state (or, for an event, the event `type`; nullptr = just
    // announce the event entity with its types). True if the cloud accepted it.
    bool publishEntity(Entity &e, const char *type) {
        char json[512];
        JSONBufferWriter w(json, sizeof(json) - 1);
        w.beginObject();
        if (e.name) w.name("n").value(e.name);
        switch (e.kind) {
            case LIGHT: {
                char hex[7];
                snprintf(hex, sizeof(hex), "%06lx", (unsigned long)(e.light.rgb & 0xFFFFFF));
                w.name("on").value(e.light.on ? 1 : 0);
                w.name("bri").value((int)e.light.brightness);
                w.name("rgb").value(hex);
                if (e.listCount) {
                    w.name("fx").value(e.list[e.light.effect < e.listCount ? e.light.effect : 0]);
                    w.name("fxs").beginArray();
                    for (uint8_t i = 0; i < e.listCount; i++) w.value(e.list[i]);
                    w.endArray();
                }
                w.name("cmd").value("ha");
                break;
            }
            case SWITCH:
                w.name("on").value(e.on ? 1 : 0);
                w.name("cmd").value("ha");
                break;
            case BINARY:
                w.name("on").value(e.on ? 1 : 0);
                if (e.dc) w.name("dc").value(e.dc);
                break;
            case NUMBER:
                w.name("v").value((double)e.num);
                w.name("min").value((double)e.min);
                w.name("max").value((double)e.max);
                w.name("step").value((double)e.step);
                if (e.unit) w.name("u").value(e.unit);
                w.name("cmd").value("ha");
                break;
            case BUTTON:
                w.name("cmd").value("ha");
                break;
            case EVENT:
                if (type) w.name("t").value(type);
                w.name("ts").beginArray();
                for (uint8_t i = 0; i < e.listCount; i++) w.value(e.list[i]);
                w.endArray();
                break;
        }
        w.endObject();
        json[std::min(w.dataSize(), sizeof(json) - 1)] = 0;
        char ev[64];
        snprintf(ev, sizeof(ev), "ha/%s/%s", kindName(e.kind), e.key);
        lastSend = millis();
        if (!Particle.publish(ev, json, PRIVATE)) return false;
        if (!type) {
            e.dirty = false;
            e.sentAt = millis() ? millis() : 1;
        }
        return true;
    }

    // sensors
    static int decimalsOf(const char *s) {
        const char *dot = strchr(s, '.');
        return dot ? (int)strlen(dot + 1) : 0;
    }
    Slot *slot(const char *key) {
        for (int i = 0; i < valueCount; i++)
            if (!strcmp(values[i].key, key)) return &values[i];
        if (valueCount >= MAX_VALUES) return nullptr;
        Slot &s = values[valueCount++];
        strlcpy(s.key, key, sizeof(s.key));
        return &s;
    }
    void setValue(const char *key, const char *val, const char *unit, const char *dc,
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
    void publishValue(Slot &s) {
        char json[256];
        JSONBufferWriter w(json, sizeof(json) - 1);
        w.beginObject();
        if (s.isText) w.name("v").value(s.val);
        else w.name("v").value(atof(s.val), decimalsOf(s.val));
        if (s.unit[0]) w.name("u").value(s.unit);
        if (s.dc[0]) w.name("dc").value(s.dc);
        if (s.sc[0]) w.name("sc").value(s.sc);
        if (s.name[0]) w.name("n").value(s.name);
        w.endObject();
        json[std::min(w.dataSize(), sizeof(json) - 1)] = 0;
        char ev[64];
        snprintf(ev, sizeof(ev), "ha/%s", s.key);
        lastSend = millis();
        if (Particle.publish(ev, json, PRIVATE)) {
            s.dirty = false;
            s.sentAt = millis() ? millis() : 1;
        }
    }
};

inline HomeAssistantClass HA;
