// Example: everything the Home Assistant "particle" integration picks up.
#include "Particle.h"
#include "HomeAssistant.h"

SYSTEM_THREAD(ENABLED);
SerialLogHandler logHandler;

int counter = 0;           // cloud variable  -> HA sensor "counter" (polled)
bool led = false;

int setLed(String arg) {   // cloud function  -> HA button "led" + particle.call_function
    led = arg == "on" || (arg == "" && !led);
    digitalWrite(D7, led);
    HA.value("led", led ? "on" : "off");
    return led;
}

void fromHa(const char *event, const char *data) {  // particle.publish_event from HA
    Log.info("HA says %s = %s", event, data);
    HA.value("last_command", data);
}

void setup() {
    pinMode(D7, OUTPUT);
    Particle.variable("counter", counter);
    Particle.function("led", setLed);
    Particle.subscribe("home/", fromHa);
    HA.name("uptime", "Uptime");
}

void loop() {
    static uint32_t last = 0;
    if (millis() - last > 30000) {
        last = millis();
        counter++;
        HA.value("uptime", (int)(millis() / 1000), "s", "duration", "total_increasing");
        HA.value("rssi", WiFi.RSSI().getStrength(), "%", nullptr, "measurement");
    }
    HA.loop();  // sends queued ha/<key> events at Particle's 1/s limit
}
