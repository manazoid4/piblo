// Piblo bootstrap - LCD test-pattern endpoint (320x172 RGB565, ~105 KB).
#include "piblo_screen.h"

namespace shot {
namespace {
uint32_t gRequests = 0;
}  // namespace

uint32_t requests() { return gRequests; }

void serve(WiFiClient& c) {
    ++gRequests;
    const uint32_t n = 320u * 172u * 2u;
    c.print("HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: ");
    c.print(n);
    c.print("\r\nConnection: close\r\n\r\n");
    uint8_t buf[2048];
    uint32_t sent = 0;
    while (sent < n) {
        size_t chunk = (n - sent > sizeof buf) ? sizeof buf : (n - sent);
        for (size_t i = 0; i < chunk; i += 2) {
            uint32_t px = (sent + i) / 2;
            uint16_t v = ((px / 40) & 1) ? 0xFFFFu : 0x0000u;
            buf[i] = (uint8_t)(v >> 8);
            buf[i + 1] = (uint8_t)(v & 0xFF);
        }
        size_t w = c.write(buf, chunk);
        if (w == 0) break;
        sent += w;
    }
}

}  // namespace shot
