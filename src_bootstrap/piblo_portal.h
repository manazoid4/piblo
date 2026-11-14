// Piblo bootstrap - HTTP portal: Wi-Fi setup + authenticated OTA update.
//
// One server, both jobs. The portal is served when the setup AP is up; OTA is
// accepted whenever the board has an IP and presents a valid token. Firmware is
// written to the inactive OTA slot and the bootloader falls back automatically
// if the new image fails to mark itself valid.
#pragma once
#include <Arduino.h>

namespace portal {

void begin();
void update();                    // call every loop
bool active();
String ip();
String otaToken();                // generated once, shown on screen + serial
bool updating();

// Human-readable last status, safe for the LCD and serial.
const char* lastStatus();

}  // namespace portal
