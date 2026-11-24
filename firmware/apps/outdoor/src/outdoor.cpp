// Outdoor temperature and humidity over cellular, in Home Assistant, on a battery.
//
// Hardware: a cellular Particle device (Boron, B-SoM, M-SoM) with an SHT40/41/45 on I2C
// and a LiPo. It wakes every WAKE_MINUTES, measures, sends one `ha/batch` publish with
// temperature, humidity, dew point, battery and signal, and sleeps again.
//
// Data Operations: one publish per wake, so 15 minutes is 96 a day, about 2,900 a month
// (the free plan has 100,000 a month for the whole account). Halving WAKE_MINUTES doubles
// that. Cellular data: the modem stays registered while the device sleeps (network
// standby), so each wake only resumes the cloud session instead of a full reconnect,
// which keeps both data use and wake time down; for wakes an hour or more apart, turning
// the modem off (drop the .network() line) saves battery instead.
//
// While it sleeps it can't take commands, so this app only reports. In Home Assistant the
// sensors keep their last value between wakes, and the device shows offline most of the time.

#include "Particle.h"
#include "HomeAssistant.h"
#include <math.h>

SYSTEM_MODE(AUTOMATIC);
SYSTEM_THREAD(ENABLED);
SerialLogHandler logHandler(LOG_LEVEL_INFO);

static const uint32_t WAKE_MINUTES = 15;
static const uint32_t MAX_AWAKE_MS = 90 * 1000;  // give up on this wake if the cloud isn't reached

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

static void measure() {
    float t, rh;
    if (readSht4x(t, rh)) {
        HA.value("temperature", t, "°C", "temperature", "measurement", 1);
        HA.value("humidity", rh, "%", "humidity", "measurement", 0);
        if (rh > 0) HA.value("dew_point", dewPoint(t, rh), "°C", "temperature", "measurement", 1);
    } else {
        Log.warn("no SHT4x at 0x44");
    }
    float soc = System.batteryCharge();  // -1 without a battery / fuel gauge
    if (soc >= 0) HA.value("battery", (int)lroundf(soc), "%", "battery", "measurement");
    CellularSignal sig = Cellular.RSSI();
    if (sig.getStrength() >= 0) HA.value("signal", (int)lroundf(sig.getStrength()), "%", nullptr, "measurement");
}

static void sleepUntilNextWake() {
    SystemSleepConfiguration config;
    config.mode(SystemSleepMode::ULTRA_LOW_POWER)
        .duration(std::chrono::minutes(WAKE_MINUTES))
        .network(NETWORK_INTERFACE_CELLULAR);  // network standby: quick, cheap reconnects
    System.sleep(config);
}

void setup() {
    Wire.begin();
    HA.refreshSeconds = 24 * 3600;  // every wake sends fresh values anyway
}

void loop() {
    static uint32_t wokeAt = millis();
    static bool measured = false;
    if (!measured) {
        measure();  // queue this wake's readings: they go out together as one ha/batch
        measured = true;
    }
    HA.loop();
    bool done = Particle.connected() && !HA.pending();
    if (done || millis() - wokeAt > MAX_AWAKE_MS) {
        if (done) delay(1000);  // let the publish leave before the radio idles
        sleepUntilNextWake();
        wokeAt = millis();     // ULTRA_LOW_POWER resumes here, in loop()
        measured = false;
    }
}
