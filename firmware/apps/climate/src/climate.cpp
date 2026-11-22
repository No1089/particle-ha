// Temperature and humidity in Home Assistant from an SHT40/41/45 on I2C (SDA/SCL).
// Any Particle device. Three sensors (temperature, humidity, dew point) and a number to
// set how often it measures.

#include "Particle.h"
#include "HomeAssistant.h"
#include <math.h>

SYSTEM_THREAD(ENABLED);
SerialLogHandler logHandler(LOG_LEVEL_INFO);

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

static float dewPoint(float t, float rh) {  // Magnus formula
    const float a = 17.62f, b = 243.12f;
    float g = logf(rh / 100.0f) + a * t / (b + t);
    return b * g / (a - g);
}

void setup() {
    Wire.begin();
    HA.addNumber("interval", "Measure every", 10, 600, 10, 60, "s", nullptr);
    HA.begin();
}

void loop() {
    static uint32_t last = 0;
    if (last == 0 || millis() - last >= HA.number("interval") * 1000) {
        last = millis();
        float t, rh;
        if (readSht4x(t, rh)) {
            HA.value("temperature", t, "°C", "temperature", "measurement", 1);
            HA.value("humidity", rh, "%", "humidity", "measurement", 0);
            if (rh > 0) HA.value("dew_point", dewPoint(t, rh), "°C", "temperature", "measurement", 1);
        } else {
            Log.warn("no SHT4x at 0x44");
        }
    }
    HA.loop();
}
