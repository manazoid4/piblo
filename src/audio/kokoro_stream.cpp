#include "kokoro_stream.h"

#include <HTTPClient.h>
#include <M5Unified.h>
#include <WiFi.h>

#include "../core/settings.h"
#include "../input/keyboard.h"
#include "limiter.h"
#include "live.h"
#include "voice.h"

namespace maz {
namespace kokoro {
namespace {

constexpr uint16_t kPort   = 8880;
constexpr uint32_t kRate   = 24000;
constexpr size_t   kChunk  = 1024;
constexpr uint32_t kIdleMs = 5000;
constexpr uint8_t  kCh     = 0;  // voice.cpp kPlayCh

// playRaw keeps the pointer, so each queued chunk needs its own memory.
alignas(4) uint8_t gBuf[3][kChunk];

String urlEncode(const String& s) {
    String out;
    char   b[4];
    for (size_t i = 0; i < s.length(); i++) {
        const uint8_t c = s[i];
        if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
            out += (char)c;
        } else {
            snprintf(b, sizeof b, "%%%02X", c);
            out += b;
        }
    }
    return out;
}

// Drains key events; true if any key was pressed (releases ignored).
bool keyHit() {
    KB.update();
    KeyEvent e;
    bool     hit = false;
    while (KB.pop(e))
        if (e.down) hit = true;
    return hit;
}

}  // namespace

bool speak(const String& text) {
    if (WiFi.status() != WL_CONNECTED || text.isEmpty()) return false;
    if (live::phase() != live::Phase::Off) return false;  // live session owns the codec

    const uint32_t t0 = millis();
    HTTPClient     http;
    http.useHTTP10(true);  // no chunked encoding: getStreamPtr() does not de-chunk
    http.setTimeout(5000);
    const String url = String("http://") + Cfg.hostAddr.c_str() + ":" + kPort + "/?text=" + urlEncode(text);
    if (!http.begin(url) || http.GET() != HTTP_CODE_OK) {
        Serial.println("[kokoro] http failed");
        http.end();
        return false;
    }

    voice::stopPlayback();
    voice::useSpeaker();

    WiFiClient* s       = http.getStreamPtr();
    int         remain  = http.getSize();  // -1 when unknown
    size_t      total   = 0, fill = 0;
    uint8_t     idx     = 0;
    bool        aborted = false, any = false;
    uint32_t    firstMs = 0, lastData = millis();

    auto queue = [&](size_t bytes) {
        while (M5.Speaker.isPlaying(kCh) > 1) {  // <=2 queued, current fill buffer never queued
            if (keyHit()) { aborted = true; return; }
            delay(1);
        }
        audio::softLimit((int16_t*)gBuf[idx], bytes / 2);
        M5.Speaker.playRaw((const int16_t*)gBuf[idx], bytes / 2, kRate, false, 1, kCh, false);
        if (!any) { any = true; firstMs = millis() - t0; }
        idx = (idx + 1) % 3;
    };

    while (!aborted && (http.connected() || s->available()) && remain != 0) {
        if (keyHit()) { aborted = true; break; }
        size_t room = kChunk - fill;
        if (remain > 0 && (size_t)remain < room) room = remain;
        const int avail = s->available();
        if (avail <= 0) {
            if (millis() - lastData > kIdleMs) { aborted = true; break; }
            delay(1);
            continue;
        }
        const int n = s->readBytes(gBuf[idx] + fill, min((size_t)avail, room));
        if (n <= 0) continue;
        lastData = millis();
        total += n;
        fill += n;
        if (remain > 0) remain -= n;
        if (fill == kChunk) {
            queue(fill);
            fill = 0;
        }
    }
    // Final partial chunk: even bytes only (an odd trailing byte is dropped).
    if (!aborted && fill >= 2) queue(fill & ~(size_t)1);
    http.end();

    while (!aborted && M5.Speaker.isPlaying(kCh)) {
        if (keyHit()) aborted = true;
        else delay(5);
    }
    if (aborted) M5.Speaker.stop(kCh);
    Serial.printf("[kokoro] bytes=%u first_audio_ms=%u aborted=%d\n", (unsigned)total, (unsigned)firstMs, aborted);
    return any || aborted;
}

}  // namespace kokoro
}  // namespace maz
