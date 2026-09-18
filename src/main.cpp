#include <Arduino.h>

#ifndef BUTTON_PIN
#define BUTTON_PIN 0
#endif

#ifndef DEBOUNCE_MS
#define DEBOUNCE_MS 40
#endif

static bool stableState = HIGH;
static bool lastReading = HIGH;
static uint32_t lastChangeMs = 0;

void setup() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  Serial.begin(115200);

  // Do not wait forever: the trigger must work even if the host opens the
  // serial port after the ESP32 has booted.
  const uint32_t waitStarted = millis();
  while (!Serial && millis() - waitStarted < 1500) {
    delay(10);
  }

  stableState = digitalRead(BUTTON_PIN);
  lastReading = stableState;
  Serial.printf("READY button_gpio=%d active=LOW\n", BUTTON_PIN);
}

void loop() {
  const bool reading = digitalRead(BUTTON_PIN);
  const uint32_t now = millis();

  if (reading != lastReading) {
    lastReading = reading;
    lastChangeMs = now;
  }

  if ((now - lastChangeMs) >= DEBOUNCE_MS && reading != stableState) {
    stableState = reading;
    if (stableState == LOW) {
      Serial.println("SNAP");
    }
  }

  delay(1);
}

