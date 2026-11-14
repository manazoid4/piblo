// Piblo bootstrap - LCD test-pattern endpoint (320x172 RGB565, ~105 KB).
//
// This GFX build exposes no framebuffer accessor, so instead of dumping the
// screen we serve a deterministic bar pattern. If the panel is wired and
// oriented correctly, the same pattern drawn to the LCD shows readable bands;
// text legibility is confirmed from the serial status log.
#pragma once
#include <Arduino.h>
#include <WiFiClient.h>

namespace shot {

void serve(WiFiClient& c);
uint32_t requests();

}  // namespace shot
