#include <Arduino.h>
#line 1 "/home/arduino/ArduinoApps/arduinomadhacks/sketch/sketch.ino"
// Dig In - MCU side (STM32)
// Reads the Modulino Movement every 16 ms (62.5 Hz) and sends the
// acceleration (in g) to Python over the Bridge.

#include <Arduino_RouterBridge.h>
#include <Modulino.h>

ModulinoMovement imu;

const unsigned long SAMPLE_MS = 16;
unsigned long lastSample = 0;

#line 13 "/home/arduino/ArduinoApps/arduinomadhacks/sketch/sketch.ino"
void setup();
#line 21 "/home/arduino/ArduinoApps/arduinomadhacks/sketch/sketch.ino"
void loop();
#line 13 "/home/arduino/ArduinoApps/arduinomadhacks/sketch/sketch.ino"
void setup() {
  Bridge.begin();
  // The Qwiic connector on the UNO Q is the second I2C bus (Wire1).
  // If this line fails to compile, change it to: Modulino.begin();
  Modulino.begin(Wire1);
  imu.begin();
}

void loop() {
  unsigned long now = millis();
  if (now - lastSample < SAMPLE_MS) return;
  lastSample = now;

  imu.update();
  Bridge.notify("record_sensor_movement", imu.getX(), imu.getY(), imu.getZ());
}
