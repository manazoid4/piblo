// Piblo — firmware entry point for the M5Stack Cardputer ADV.
#include <M5Unified.h>
#include <sys/time.h>
#include <time.h>

#include "audio/sfx.h"
#include "audio/voice.h"
#include "core/ambient.h"
#include "core/field.h"
#include "core/health.h"
#include "core/bench.h"
#include "core/notify.h"
#include "core/settings.h"
#include "core/launcher.h"
#include "core/shell.h"
#include "core/sys.h"
#include "input/keyboard.h"
#include "net/board_link.h"
#include "net/control.h"
#include "net/mazhost.h"
#include "net/net.h"
#include "net/portal.h"
#include "storage/store.h"

using namespace maz;

void setup() {
    Serial.begin(115200);
    Serial.println("[boot] serial");

    auto cfg          = M5.config();
    cfg.internal_spk  = true;
    cfg.internal_mic  = true;
    cfg.internal_imu  = true;
    cfg.clear_display = true;
    M5.begin(cfg);
    M5.Display.setRotation(1);
    M5.Display.setBrightness(180);
    Serial.printf("[boot] display board=%d\n", (int)M5.getBoard());

    Sys.bootMillis = millis();

    Cfg.load();
    host::fwBootGuard();
    Serial.println("[boot] settings");

    if (!KB.begin())
        ESP_LOGE("maz", "keyboard did not init - is this really a Cardputer ADV?");
    Serial.printf("[boot] keyboard=%s\n", KB.ok() ? "ok" : "missing");

    store::begin();
    Serial.printf("[boot] storage=%s\n", store::backendName());
    voice::begin();
    Serial.println("[boot] audio");
    net::begin();
    Serial.println("[boot] network");

    control::begin();
    portal::begin();
    field::begin();

    if (Cfg.lastKnownEpoch > 0) {
        timeval tv;
        tv.tv_sec  = static_cast<time_t>(Cfg.lastKnownEpoch);
        tv.tv_usec = 0;
        settimeofday(&tv, nullptr);
    }

    if (!shell::begin()) {
        Serial.println("[boot] shell failed: out of memory");
        M5.Display.fillScreen(TFT_BLACK);
        M5.Display.setTextColor(TFT_RED);
        M5.Display.drawString("Piblo: out of memory", 10, 60);
        return;
    }
    Serial.println("[boot] shell");

    health::begin();
    PIBLO_BENCH_BOOT();

    Serial.printf("Piblo %s READY board=%d keyboard=%s storage=%s\n",
                  NOD_FW_VERSION, (int)M5.getBoard(),
                  KB.ok() ? "ok" : "missing", store::backendName());

    if (!KB.ok())
        notify::post(Note::Error, "Keyboard not found", "TCA8418 did not answer");
    if (!store::ready())
        notify::post(Note::Warn, "No storage", "notes and recordings are disabled");
    else if (Sys.internalFormatted)
        notify::post(Note::Warn, "Internal storage reset", "previous notes and recordings are gone");
    if (Sys.sdUnreadable)
        notify::post(Note::Warn, "SD card unreadable", "format it as FAT32 to keep data safely");
    if (Cfg.fieldMode)
        notify::post(Note::Info, "FIELD mode restored", "lower background + faster dim");
    board::begin();    // ESP-NOW link to the Piblo board (offload); falls back to HTTP when not healthy
}

void loop() {
    const uint32_t loopStarted = millis();

    control::update();
    portal::update();
    shell::loop();
    ambient::update();
    sfx::update();     // non-blocking alert/approval/alarm chirps
    host::linkIdle();  // WiFi power saving back on 20 s after the last voice request, from any screen
    board::update();   // non-blocking ESP-NOW discovery/ping

    health::observeLoop(millis() - loopStarted);
    PIBLO_BENCH_LOOP(millis() - loopStarted);
    health::update();
    PIBLO_BENCH_REPORT();
    delay(2);
}
