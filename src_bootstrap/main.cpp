// Piblo bootstrap - Waveshare ESP32-S3-LCD-1.47B (ESP32-S3R8).
//
// One file, four jobs: LCD status, Wi-Fi STA with auto-reconnect and a
// Piblo-Setup fallback AP, token-authenticated OTA, and an ESP-NOW link to the
// M5Stack Cardputer ADV. No UI framework, no network state machine beyond what
// is needed to recover on its own.
#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <WiFi.h>
#include <esp_ota_ops.h>
#include <HTTPClient.h>
#include <Update.h>
#include <esp_now.h>

#include "piblo_cfg.h"
#include "piblo_ver.h"
#include "piblo_now.h"
#include "piblo_portal.h"

// Waveshare ESP32-S3-LCD-1.47B: ST7789 172x320, column offset 34.
Arduino_ESP32SPI gBus(41, 42, 40, 45, GFX_NOT_DEFINED);
Arduino_ST7789 gDisplay(&gBus, 39, 0, true, 172, 320, 34, 0, 34, 0);

namespace {
const char* kTag = "piblo";
constexpr const char* kSetupSsid = "Piblo-Setup";
constexpr const char* kSetupPass = "piblosetup";   // open to anyone nearby; OTA is token-gated
constexpr uint32_t kReconnectMs = 8000;
constexpr uint32_t kDrawMs = 500;
constexpr uint32_t kStatusMs = 20000;
constexpr uint32_t kPairHoldMs = 5000;
constexpr uint8_t kBootKey = 0;                    // GPIO0 = BOOT button

bool gWifiOn = false;
bool gSetupAp = false;
bool gStation = false;
uint32_t gNextReconnect = 0;
uint32_t gNextDraw = 0;
uint32_t gNextStatus = 0;
uint32_t gHoldStart = 0;
bool gPairing = false;
uint32_t gPairUntil = 0;
String gLastIp = "not connected";
uint8_t gSavedPeer[6] = {0};
bool gHaveSavedPeer = false;
bool gNowReady = false;
uint32_t gNextBeacon = 0;

void draw(const char* wifiLine, const char* ipLine, const char* otaLine, const char* nowLine) {
    // Panel bring-up proof: colour fields with high contrast plus the text.
    // If the ST7789 is wired correctly these read as solid, distinct blocks
    // (red/green/blue stripes) with legible text on top.
    gDisplay.fillScreen(BLACK);
    for (uint16_t x = 0; x < 172; x += 24) {
        uint16_t c = ((x / 24) % 3 == 0) ? (uint16_t)0xF800 : (((x / 24) % 3 == 1) ? (uint16_t)0x07E0 : (uint16_t)0x001F);
        gDisplay.fillRect(x, 0, 24, 20, c);
    }
    gDisplay.setTextColor(WHITE);
    gDisplay.setTextSize(2);
    gDisplay.setCursor(6, 28);
    gDisplay.println("PIBLO DEV");

    gDisplay.setTextSize(1);
    gDisplay.setTextColor((uint16_t)0x9FD8FF);
    gDisplay.setCursor(6, 52);
    gDisplay.println(wifiLine);
    gDisplay.setTextColor(WHITE);
    gDisplay.setCursor(6, 68);
    gDisplay.println(ipLine);

    gDisplay.setTextColor((uint16_t)0xFFC04D);
    gDisplay.setCursor(6, 88);
    gDisplay.println(otaLine);

    gDisplay.setTextColor(pnow::peerFresh() ? (uint16_t)0x42D58A : (uint16_t)0xFF676D);
    gDisplay.setCursor(6, 108);
    gDisplay.println(nowLine);

    gDisplay.setTextColor((uint16_t)0x6B7784);
    gDisplay.setCursor(6, 136);
    gDisplay.print("FW ");
    gDisplay.println(PIBLO_BOOT_VERSION);
}

void setWifiLines(char* wifiLine, size_t wl, char* ipLine, size_t il) {
    if (gStation && WiFi.status() == WL_CONNECTED) {
        snprintf(wifiLine, wl, "Wi-Fi: %s", WiFi.SSID().c_str());
        snprintf(ipLine, il, "IP: %s", WiFi.localIP().toString().c_str());
    } else if (gSetupAp) {
        snprintf(wifiLine, wl, "Setup AP: %s", kSetupSsid);
        snprintf(ipLine, il, "Setup IP: %s", WiFi.softAPIP().toString().c_str());
    } else {
        snprintf(wifiLine, wl, "Wi-Fi: offline");
        snprintf(ipLine, il, "IP: -");
    }
}

void statusLines(char* otaLine, size_t ol, char* nowLine, size_t nl) {
    snprintf(otaLine, ol, "OTA: %s", portal::lastStatus());
    if (!pnow::peerLinked() && !gPairing) {
        snprintf(nowLine, nl, "ESP-NOW: %s", gNowReady ? "ready (hold BOOT)" : "init failed");
    } else if (gPairing) {
        snprintf(nowLine, nl, "ESP-NOW: pairing...");
    } else {
        snprintf(nowLine, nl, "ESP-NOW: %s ch%d ack %u/%u",
                 pnow::peerFresh() ? "linked" : "stale",
                 pnow::channel(), pnow::ackCount(), pnow::sentCount());
    }
}

void refresh() {
    char wl[40], il[24], ol[30], nl[44];
    setWifiLines(wl, sizeof wl, il, sizeof il);
    statusLines(ol, sizeof ol, nl, sizeof nl);
    draw(wl, il, ol, nl);
    Serial.printf("piblo: %s | %s | %s | %s\n", wl, il, ol, nl);
}

void startSetupAp() {
    if (gSetupAp) return;
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(kSetupSsid, kSetupPass);
    gSetupAp = true;
    Serial.printf("piblo: setup AP %s up at %s\n", kSetupSsid, WiFi.softAPIP().toString().c_str());
}

void stopSetupAp() {
    if (!gSetupAp) return;
    WiFi.softAPdisconnect(true);
    gSetupAp = false;
    if (gStation) WiFi.mode(WIFI_STA);
}

bool connectSaved() {
    cfg::Settings s;
    if (!cfg::load(s) || s.ssid[0] == 0) return false;
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(s.ssid, s.pass);
    gWifiOn = true;
    gStation = true;
    Serial.printf("piblo: joining %s\n", s.ssid);
    uint32_t deadline = millis() + 12000;
    while (millis() < deadline && WiFi.status() != WL_CONNECTED) delay(100);
    if (WiFi.status() == WL_CONNECTED) {
        stopSetupAp();
        pnow::syncChannel();
        return true;
    }
    return false;
}

void attachSavedPeer() {
    cfg::Settings s;
    if (!cfg::load(s) || !s.hasPeer) return;
    memcpy(gSavedPeer, s.peer, 6);
    gHaveSavedPeer = true;
    pnow::addPeer(gSavedPeer, -1);
    Serial.println("piblo: saved Cardputer peer attached");
}

// While BOOT is held we broadcast a PeerHello so a Cardputer in pairing mode
// can capture our MAC and store it. The Cardputer replies with its own peer
// frame; we adopt the first non-broadcast sender as our peer.
bool peerIsAllZero(const uint8_t* p) {
    for (int i = 0; i < 6; ++i) if (p[i]) return false;
    return true;
}

void onEspNowRx(const pnow::Message& msg, const uint8_t peer[6]) {
    switch ((pnow::Kind)msg.kind) {
        case pnow::Kind::Ping:
            break;  // ACK already sent by the radio layer
        case pnow::Kind::PeerHello:
        case pnow::Kind::DeviceStatus:
            if (!gHaveSavedPeer && !peerIsAllZero(peer)) {
                memcpy(gSavedPeer, peer, 6);
                gHaveSavedPeer = true;
                cfg::savePeer(gSavedPeer);
                pnow::addPeer(gSavedPeer, -1);
                Serial.println("piblo: peer learned from hello");
            }
            break;
        case pnow::Kind::ButtonEvent:
            Serial.printf("piblo: button %u %s\n", msg.data[0], msg.data[1] ? "down" : "up");
            break;
        case pnow::Kind::DisplayMessage:
            Serial.printf("piblo: display '%.*s'\n", (int)msg.len, msg.data);
            break;
        default:
            break;
    }
    if (gPairing && (pnow::Kind)msg.kind == pnow::Kind::Pong) {
        gPairing = false;
        Serial.println("piblo: pairing confirmed by peer");
    }
}

void beginPairing() {
    if (gPairing) return;
    gPairing = true;
    gPairUntil = millis() + kPairHoldMs;
    pnow::Message hello;
    pnow::makePeerHello(hello, "piblo-bootstrap");
    pnow::sendRaw(nullptr, hello);      // nullptr = broadcast
    Serial.println("piblo: pairing hello broadcast");
}

// USB control channel. Lets a host configure Wi-Fi, read the OTA token and
// trigger an OTA fetch without needing a second Wi-Fi radio. Credentials are
// written straight to NVS and never echoed.
//   PIBLO WIFI <ssid> <pass>
//   PIBLO TOKEN
//   PIBLO OTA <url>
//   PIBLO STATUS
//   PIBLO FORGET
String gCmdLine;
bool gCmdOverflow = false;

void serialControl() {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == 13) continue;
        if (c != 10) {
            if (gCmdLine.length() < 200 && !gCmdOverflow) gCmdLine += c;
            else gCmdOverflow = true;
            continue;
        }
        if (gCmdOverflow) { gCmdLine = ""; gCmdOverflow = false; continue; }
        String cmd = gCmdLine;
        gCmdLine = "";
        cmd.trim();
        if (!cmd.startsWith("PIBLO ")) continue;
        String rest = cmd.substring(6);
        if (rest.startsWith("WIFI ")) {
            int sp = rest.indexOf(' ', 5);
            String ssid = rest.substring(5, sp < 0 ? rest.length() : sp);
            String pass = sp < 0 ? "" : rest.substring(sp + 1);
            if (ssid.length() >= 1 && ssid.length() <= 32 && pass.length() <= 63) {
                cfg::saveWifi(ssid.c_str(), pass.c_str());
                Serial.println("piblo: wifi saved, rebooting");
                delay(200);
                ESP.restart();
            } else {
                Serial.println("piblo: wifi rejected (bad length)");
            }
        } else if (rest == "SCAN") {
            Serial.println("piblo: scanning");
            int n = WiFi.scanNetworks(false, false, false, 300, 0);
            Serial.printf("piblo: scan found %d\n", n);
            for (int i = 0; i < n; ++i) {
                Serial.printf("piblo: net %s ch%d %d dBm %s\n",
                              WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i),
                              WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "open" : "secure");
            }
            WiFi.scanDelete();
        } else if (rest == "LOOP") {
            // Firmware-side serial RX test: echoes back exactly what arrived.
            Serial.printf("piblo: loop rx=%u '%s'\n", gCmdLine.length(), gCmdLine.c_str());
        } else if (rest == "NET") {
            Serial.printf("piblo: mode=%d sta=%d ap=%d ch=%d ip=%s\n",
                          (int)WiFi.getMode(), (int)WiFi.status(), (int)gSetupAp,
                          pnow::channel(), WiFi.localIP().toString().c_str());
        } else if (rest == "TOKEN") {
            Serial.printf("piblo: ota token %s\n", portal::otaToken().c_str());
        } else if (rest.startsWith("OTA ")) {
            String url = rest.substring(4);
            url.trim();
            Serial.printf("piblo: ota url accepted\n");
            WiFiClient client;
            HTTPClient http;
            http.setConnectTimeout(3000);
            http.setTimeout(60000);
            if (!http.begin(client, url)) { Serial.println("piblo: ota url invalid"); return; }
            int code = http.GET();
            if (code != 200) { Serial.printf("piblo: ota http %d\n", code); http.end(); return; }
            int len = http.getSize();
            WiFiClient* s = http.getStreamPtr();
            if (!Update.begin(len, U_FLASH)) { Serial.println("piblo: ota no slot"); http.end(); return; }
            uint8_t buf[1024];
            size_t got = 0;
            uint32_t last = millis();
            while (s->connected() && got < (size_t)len && (int32_t)(millis() - last) < 60000) {
                int avail = s->available();
                if (avail <= 0) { delay(1); continue; }
                int n = s->read(buf, (size_t)min((int)sizeof buf, avail));
                if (n <= 0) continue;
                if (Update.write(buf, n) != n) { Update.abort(); Serial.println("piblo: ota write fail"); http.end(); return; }
                got += n;
                last = millis();
            }
            http.end();
            if (got != (size_t)len || !Update.end(true)) { Serial.println("piblo: ota verify fail"); return; }
            const esp_partition_t* part = esp_ota_get_next_update_partition(nullptr);
            if (part && esp_ota_set_boot_partition(part) == ESP_OK) {
                Serial.println("piblo: ota ok, rebooting");
                delay(300);
                ESP.restart();
            } else {
                Serial.println("piblo: ota set boot failed");
            }
        } else if (rest == "STATUS") {
            char wl[40], il[24], ol[30], nl[44];
            setWifiLines(wl, sizeof wl, il, sizeof il);
            statusLines(ol, sizeof ol, nl, sizeof nl);
            Serial.printf("piblo: %s | %s | %s | %s\n", wl, il, ol, nl);
        } else if (rest == "FORGET") {
            cfg::clearAll();
            Serial.println("piblo: settings cleared, rebooting");
            delay(200);
            ESP.restart();
        } else {
            Serial.println("piblo: unknown command");
        }
    }
}
}  // namespace

void setup() {
    Serial.begin(115200);
    delay(300);
    Serial.printf("\npiblo: bootstrap %s starting\n", PIBLO_BOOT_VERSION);

    gDisplay.begin();
    pinMode(46, OUTPUT);
    digitalWrite(46, HIGH);
    gDisplay.setRotation(1);
    // Panel self-test before the status screen: three solid fields. If the
    // ST7789 is wired correctly the top of the panel is red, green, blue.
    gDisplay.fillRect(0, 0, 172, 60, (uint16_t)0xF800);
    gDisplay.fillRect(0, 60, 172, 60, (uint16_t)0x07E0);
    gDisplay.fillRect(0, 120, 172, 60, (uint16_t)0x001F);
    gDisplay.setTextColor(WHITE);
    gDisplay.setTextSize(2);
    gDisplay.setCursor(8, 96);
    gDisplay.println("SELFTEST");
    delay(1200);
    draw("Wi-Fi: starting", "IP: -", "OTA: starting", "ESP-NOW: starting");

    pinMode(kBootKey, INPUT_PULLUP);

    WiFi.setAutoReconnect(true);
    WiFi.persistent(false);
    // ESP-NOW needs a station-mode interface to send on. In pure AP mode
    // esp_now_init() fails, so always keep the STA interface up.
    WiFi.mode(WIFI_AP_STA);

    if (!cfg::hasWifi()) {
        Serial.println("piblo: no saved Wi-Fi, starting setup AP");
        startSetupAp();
    } else if (!connectSaved()) {
        Serial.println("piblo: saved network unreachable, setup AP available");
        startSetupAp();
    }

    gNowReady = pnow::begin(onEspNowRx);
    Serial.printf("piblo: ESP-NOW %s (ch%d)\n", gNowReady ? "ready" : "init failed", pnow::channel());
    attachSavedPeer();

    portal::begin();
    Serial.printf("piblo: portal up, OTA token ready\n");
    refresh();
}

void loop() {
    pnow::update();
    portal::update();
    serialControl();

    // Wi-Fi housekeeping: reconnect if the link dropped.
    if (gStation && WiFi.status() != WL_CONNECTED && !gSetupAp) {
        if ((int32_t)(millis() - gNextReconnect) >= 0) {
            gNextReconnect = millis() + kReconnectMs;
            Serial.println("piblo: Wi-Fi lost, reconnecting");
            WiFi.reconnect();
        }
    }
    if (gStation && WiFi.status() == WL_CONNECTED && gSetupAp) stopSetupAp();

    // Hold BOOT to re-pair with the Cardputer.
    if (digitalRead(kBootKey) == LOW) {
        if (gHoldStart == 0) gHoldStart = millis();
        if (!gPairing && (int32_t)(millis() - gHoldStart) > 800) beginPairing();
    } else {
        gHoldStart = 0;
    }
    if (gPairing && (int32_t)(millis() - gPairUntil) >= 0) gPairing = false;

    if ((int32_t)(millis() - gNextDraw) >= 0) {
        gNextDraw = millis() + kDrawMs;
        refresh();
    }
}
