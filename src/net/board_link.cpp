// Piblo Cardputer - ESP-NOW link to the Piblo board (offload service).
#include "board_link.h"

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <mbedtls/md.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>

#include <algorithm>
#include <string.h>

#include "../core/settings.h"
#include "../core/sys.h"
#include "../storage/store.h"

namespace maz {
namespace board {

using namespace pbp;

namespace {

// ---------- Configuration / keys ----------
uint8_t gLmk[16];
uint8_t gTagKey[16];
bool gKeyed = false;

// ---------- Peer state ----------
uint8_t gPeerMac[6] = {0};
bool gHavePeer = false;
uint32_t gLastRx = 0;
uint32_t gNextHello = 0;
uint32_t gNextPing = 0;
bool gDiscovering = false;
bool gNowUp = false;      // esp_now_init succeeded (retried from update() until Wi-Fi is up)
uint32_t gNextInit = 0;
uint32_t gHelloSent = 0;

// ---------- Upload state ----------
enum UploadState : uint8_t { Idle, SendingBegin, SendingData, WaitingResult, Done };
UploadState gUpSt = Idle;

uint8_t gUpSid = 0;
uint8_t gUpKind = 0;
uint32_t gUpTotal = 0;
uint32_t gUpCrc = 0;
uint32_t gUpOffset = 0;
uint16_t gUpNextExpected = 0;  // cumulative ACK: next chunk index expected
uint32_t gUpLastAckMs = 0;
uint8_t gUpRetries = 0;
std::string gUpWavPath;
uint8_t* gUpBuf = nullptr;
size_t gUpBufSize = 0;

// Result
uint8_t gUpResCode = RES_WORKING;
uint16_t gUpResHttp = 0;
char gUpResText[96] = "";
bool gUpResultReady = false;
volatile bool gUpAbort = false;  // set by upload() on timeout so the task stops sending

// Synchronization
SemaphoreHandle_t gUpMutex = nullptr;
TaskHandle_t gUpTask = nullptr;

// Public state
State gState = {};

constexpr uint32_t kHelloIntervalMs = 1000;
constexpr uint32_t kPingIntervalMs = 2000;
constexpr uint32_t kPeerTimeoutMs = 6000;
constexpr uint32_t kAckTimeoutMs = 500;
constexpr uint8_t kMaxRetries = 5;
constexpr uint32_t kResultPollMs = 500;
constexpr uint32_t kResultTimeoutMs = 35000;  // overall timeout for upload+forward

// ---------- Helpers ----------

void ownMac(uint8_t m[6]) {
    WiFi.macAddress(m);
}

void deriveKey() {
    const std::string& t = Cfg.hostToken;  // Core token is stored as hostToken
    gKeyed = t.length() >= 8;
    if (!gKeyed) return;
    std::string s = "piblo-board-v1|" + t;
    uint8_t h[32];
    mbedtls_md(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256),
               (const uint8_t*)s.c_str(), s.length(), h);
    memcpy(gLmk, h, 16);
    memcpy(gTagKey, h + 16, 16);
}

void makeTag(char who, const uint8_t mac[6], uint8_t out[8]) {
    uint8_t msg[7] = {(uint8_t)who};
    memcpy(msg + 1, mac, 6);
    uint8_t full[32];
    mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256),
                    gTagKey, 16, msg, 7, full);
    memcpy(out, full, 8);
}

bool addEncryptedPeer(const uint8_t mac[6]) {
    if (gHavePeer && memcmp(gPeerMac, mac, 6) == 0) return true;
    if (gHavePeer) esp_now_del_peer(gPeerMac);
    // Also delete any unencrypted entry for the same MAC
    esp_now_del_peer(mac);

    esp_now_peer_info_t p = {};
    memcpy(p.peer_addr, mac, 6);
    p.ifidx = WIFI_IF_STA;
    p.channel = 0;  // follow station channel
    p.encrypt = true;
    memcpy(p.lmk, gLmk, 16);
    if (esp_now_add_peer(&p) != ESP_OK) {
        gHavePeer = false;
        return false;
    }
    memcpy(gPeerMac, mac, 6);
    gHavePeer = true;
    return true;
}

void sendRaw(const uint8_t* mac, const uint8_t* data, size_t len) {
    esp_now_send(mac, data, len);
}

void sendToPeer(const uint8_t* data, size_t len) {
    if (gHavePeer) sendRaw(gPeerMac, data, len);
}

// Broadcast discovery HELLO
void sendHello() {
    if (!gKeyed) return;
    ++gHelloSent;
    uint8_t mac[6];
    ownMac(mac);
    uint8_t frame[10] = {T_DISC, D_HELLO};
    makeTag('C', mac, frame + 2);
    uint8_t bc[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    sendRaw(bc, frame, sizeof(frame));
}

// Send PING
void sendPing() {
    if (!gHavePeer) return;
    uint8_t frame[2] = {T_PING, 0};
    sendToPeer(frame, sizeof(frame));
}

// Send BEGIN
void sendBegin(uint8_t sid, uint8_t kind, uint32_t total, uint32_t crc) {
    uint8_t frame[11] = {T_BEGIN, sid, kind};
    put32(frame + 3, total);
    put32(frame + 7, crc);
    sendToPeer(frame, sizeof(frame));
}

// Send DATA chunk
void sendData(uint8_t sid, uint16_t idx, const uint8_t* data, size_t len) {
    uint8_t frame[4 + 240];
    frame[0] = T_DATA;
    frame[1] = sid;
    frame[2] = idx & 0xFF;
    frame[3] = idx >> 8;
    memcpy(frame + 4, data, len);
    sendToPeer(frame, 4 + len);
}

// Send QUERY
void sendQuery(uint8_t sid) {
    uint8_t frame[2] = {T_QUERY, sid};
    sendToPeer(frame, sizeof(frame));
}

// ESP-NOW callbacks
void onReceive(const uint8_t* mac, const uint8_t* data, int len) {
    if (!mac || !data || len < 1) return;
    uint8_t t = data[0];

    // Broadcast discovery frames
    if (t == T_DISC) {
        if (len == 13 && data[1] == D_BEACON) {
            // BEACON: B0 02 ver_major ver_minor ver_patch tag[8]
            if (!gKeyed) return;
            uint8_t want[8];
            makeTag('B', mac, want);
            if (memcmp(want, data + 5, 8) == 0) {
                // Valid BEACON from board - pair with it
                if (addEncryptedPeer(mac)) {
                    gLastRx = millis();
                    gState.linked = true;
                    memcpy(gState.boardMac, mac, 6);
                    gState.haveMac = true;
                    gDiscovering = false;
                    // Immediately ping to check health
                    sendPing();
                }
            }
        }
        return;
    }

    // Everything else must be from our paired peer (encrypted)
    if (!gHavePeer || memcmp(mac, gPeerMac, 6) != 0) return;
    gLastRx = millis();

    switch (t) {
        case T_PONG: {
            // B9 status (bit0 = ready)
            if (len >= 2) {
                bool ready = (data[1] & 0x01) != 0;
                gState.healthy = ready;
                gState.lastPongMs = millis();
            }
            break;
        }
        case T_ACK: {
            // B3 sid next16 status
            if (len >= 5 && gUpSt != Idle && data[1] == gUpSid) {
                uint16_t next = data[2] | (data[3] << 8);
                uint8_t status = data[4];
                if (status == 0) {
                    gUpNextExpected = next;
                    gUpOffset = (uint32_t)next * CHUNK;
                    gUpLastAckMs = millis();
                    gUpRetries = 0;
                } else if (status == 1) {
                    // Bad - will retry from current offset
                    gUpRetries++;
                } else if (status == 2) {
                    // Busy - wait and retry
                    gUpRetries++;
                }
            }
            break;
        }
        case T_RESULT: {
            // B5 sid code http16 text...
            if (len >= 5 && data[1] == gUpSid) {
                gUpResCode = data[2];
                gUpResHttp = data[3] | (data[4] << 8);
                size_t textLen = len - 5;
                if (textLen > sizeof(gUpResText) - 1) textLen = sizeof(gUpResText) - 1;
                memcpy(gUpResText, data + 5, textLen);
                gUpResText[textLen] = '\0';
                gUpResultReady = true;
            }
            break;
        }
    }
}

void onSend(const uint8_t* mac, esp_now_send_status_t status) {
    (void)mac;
    (void)status;
    // Not used for now
}

// ---------- Upload task ----------

void runUpload() {
    // Read WAV file into buffer
    File file = store::fs()->open(gUpWavPath.c_str(), FILE_READ);
    if (!file) {
        xSemaphoreTake(gUpMutex, portMAX_DELAY);
        gUpResCode = 3;
        snprintf(gUpResText, sizeof(gUpResText), "cannot open file");
        gUpResultReady = true;
        gUpSt = Done;
        xSemaphoreGive(gUpMutex);
        return;
    }

    gUpTotal = file.size();
    if (gUpTotal == 0 || gUpTotal > MAX_UPLOAD) {
        file.close();
        xSemaphoreTake(gUpMutex, portMAX_DELAY);
        gUpResCode = 3;
        snprintf(gUpResText, sizeof(gUpResText), "bad file size");
        gUpResultReady = true;
        gUpSt = Done;
        xSemaphoreGive(gUpMutex);
        return;
    }

    // Allocate buffer if needed
    if (gUpBufSize < gUpTotal) {
        if (gUpBuf) free(gUpBuf);
        gUpBuf = (uint8_t*)malloc(gUpTotal);
        if (!gUpBuf) {
            file.close();
            xSemaphoreTake(gUpMutex, portMAX_DELAY);
            gUpResCode = 3;
            snprintf(gUpResText, sizeof(gUpResText), "no memory");
            gUpResultReady = true;
            gUpSt = Done;
            xSemaphoreGive(gUpMutex);
            return;
        }
        gUpBufSize = gUpTotal;
    }

    // Read entire file
    file.read(gUpBuf, gUpTotal);
    file.close();

    // Compute CRC
    gUpCrc = crc32(gUpBuf, gUpTotal);

    xSemaphoreTake(gUpMutex, portMAX_DELAY);
    gUpSt = SendingBegin;
    gUpOffset = 0;
    gUpNextExpected = 0;
    gUpLastAckMs = millis();
    gUpRetries = 0;
    gUpResultReady = false;
    xSemaphoreGive(gUpMutex);

    const uint32_t startMs = millis();

    while (true) {
        uint32_t now = millis();
        if (gUpAbort) break;
        if (now - startMs > kResultTimeoutMs) {
            xSemaphoreTake(gUpMutex, portMAX_DELAY);
            gUpResCode = 4;
            snprintf(gUpResText, sizeof(gUpResText), "timeout");
            gUpResultReady = true;
            gUpSt = Done;
            xSemaphoreGive(gUpMutex);
            break;
        }

        xSemaphoreTake(gUpMutex, portMAX_DELAY);
        UploadState st = gUpSt;
        xSemaphoreGive(gUpMutex);

        if (st == SendingBegin) {
            sendBegin(gUpSid, gUpKind, gUpTotal, gUpCrc);
            xSemaphoreTake(gUpMutex, portMAX_DELAY);
            gUpSt = SendingData;
            gUpLastAckMs = now;
            xSemaphoreGive(gUpMutex);
        } else if (st == SendingData) {
            // Send next chunk(s) - go-back-N: we send up to 8 chunks ahead of ACK
            while (gUpOffset < gUpTotal) {
                uint16_t chunkIdx = gUpOffset / CHUNK;
                if (chunkIdx >= gUpNextExpected + 8) break;  // window full

                size_t remain = gUpTotal - gUpOffset;
                size_t chunkSize = (remain > CHUNK) ? CHUNK : remain;
                sendData(gUpSid, chunkIdx, gUpBuf + gUpOffset, chunkSize);
                gUpOffset += chunkSize;
            }

            // Check for ACK timeout
            if (now - gUpLastAckMs > kAckTimeoutMs) {
                gUpRetries++;
                if (gUpRetries >= kMaxRetries) {
                    xSemaphoreTake(gUpMutex, portMAX_DELAY);
                    gUpResCode = 3;
                    snprintf(gUpResText, sizeof(gUpResText), "ack timeout");
                    gUpResultReady = true;
                    gUpSt = Done;
                    xSemaphoreGive(gUpMutex);
                } else {
                    // Go back to last acknowledged position
                    xSemaphoreTake(gUpMutex, portMAX_DELAY);
                    gUpOffset = (uint32_t)gUpNextExpected * CHUNK;
                    gUpLastAckMs = now;
                    xSemaphoreGive(gUpMutex);
                }
            }

            // Check if all data sent and ACKed
            if (gUpOffset >= gUpTotal && gUpNextExpected * CHUNK >= gUpTotal) {
                xSemaphoreTake(gUpMutex, portMAX_DELAY);
                gUpSt = WaitingResult;
                xSemaphoreGive(gUpMutex);
            }
        } else if (st == WaitingResult) {
            // Poll for RESULT with QUERY
            static uint32_t lastQuery = 0;
            if (now - lastQuery > kResultPollMs) {
                sendQuery(gUpSid);
                lastQuery = now;
            }
            if (gUpResultReady) {
                xSemaphoreTake(gUpMutex, portMAX_DELAY);
                gUpSt = Done;
                xSemaphoreGive(gUpMutex);
            }
        } else if (st == Done) {
            break;
        }

        delay(5);
    }

}

// The buffer is only needed during an upload; keep the heap free for the HTTP fallback.
void uploadTask(void*) {
    runUpload();
    if (gUpBuf) { free(gUpBuf); gUpBuf = nullptr; gUpBufSize = 0; }
    vTaskDelete(nullptr);
}

}  // namespace

// ---------- Public API ----------

bool initNow() {
    if (WiFi.getMode() == WIFI_OFF) return false;  // ESP-NOW needs the Wi-Fi driver started
    if (esp_now_init() != ESP_OK) {
        Serial.println("[board] esp_now_init failed");
        return false;
    }
    esp_now_register_recv_cb(onReceive);
    esp_now_register_send_cb(onSend);

    // Add broadcast peer for discovery (unencrypted)
    esp_now_peer_info_t b = {};
    memset(b.peer_addr, 0xFF, 6);
    b.ifidx = WIFI_IF_STA;
    b.encrypt = false;
    esp_now_add_peer(&b);

    // Sync channel from Wi-Fi
    uint8_t primary = 0;
    wifi_second_chan_t second = WIFI_SECOND_CHAN_NONE;
    if (esp_wifi_get_channel(&primary, &second) == ESP_OK) {
        // ESP-NOW will follow the station channel when peer channel = 0
    }

    gNowUp = true;
    Serial.println("[board] ESP-NOW up, discovering");
    gDiscovering = true;
    gNextHello = millis();
    sendHello();
    return true;
}

void begin() {
    deriveKey();
    if (!gUpMutex) gUpMutex = xSemaphoreCreateMutex();
    initNow();
}

void update() {
    if (gNowUp && WiFi.getMode() == WIFI_OFF) {  // Wi-Fi switched off: ESP-NOW must be re-initialised later
        esp_now_deinit();
        gNowUp = false;
        gHavePeer = false;
        gState.linked = gState.healthy = false;
    }
    if (!gNowUp) {  // boot ran before Wi-Fi, or Wi-Fi was switched off: retry every 2 s
        if ((int32_t)(millis() - gNextInit) < 0) return;
        gNextInit = millis() + 2000;
        if (!initNow()) return;
    }
    if (!gKeyed) {
        deriveKey();
        if (!gKeyed) return;
    }

    uint32_t now = millis();

    // Discovery: send HELLO periodically until paired
    if (!gHavePeer || !gState.linked) {
        if ((int32_t)(now - gNextHello) >= 0) {
            sendHello();
            gNextHello = now + kHelloIntervalMs;
        }
        return;
    }

    // Check peer timeout
    if (now - gLastRx > kPeerTimeoutMs) {
        gState.linked = false;
        gState.healthy = false;
        gHavePeer = false;
        esp_now_del_peer(gPeerMac);
        gDiscovering = true;
        gNextHello = now;
        return;
    }

    // The board only adds us as its peer after a HELLO, and its BEACONs keep gLastRx fresh,
    // so a missed HELLO would leave us "linked" forever with every PING dropped. Keep
    // saying HELLO until the board answers a PONG.
    if (!gState.healthy && (int32_t)(now - gNextHello) >= 0) {
        sendHello();
        gNextHello = now + kHelloIntervalMs;
    }

    // Periodic PING to check health
    if ((int32_t)(now - gNextPing) >= 0) {
        sendPing();
        gNextPing = now + kPingIntervalMs;
    }

    // Health expires after 6s without PONG
    if (gState.healthy && now - gState.lastPongMs > kPeerTimeoutMs) {
        gState.healthy = false;
    }
}

const State& state() {
    return gState;
}

std::string upload(uint8_t kind, const std::string& wavPath, uint32_t timeoutMs) {
    if (!canUpload()) return "";

    // Check file exists and get size
    File file = store::fs()->open(wavPath.c_str(), FILE_READ);
    if (!file) return "";
    uint32_t fsize = file.size();
    file.close();
    if (fsize == 0 || fsize > MAX_UPLOAD) return "";

    // Prepare upload state
    xSemaphoreTake(gUpMutex, portMAX_DELAY);
    if (gUpSt != Idle && gUpSt != Done) {
        xSemaphoreGive(gUpMutex);
        return "";
    }
    gUpSid = (uint8_t)(esp_random() & 0xFF);
    if (gUpSid == 0) gUpSid = 1;
    gUpKind = kind;
    gUpWavPath = wavPath;
    gUpResCode = RES_WORKING;
    gUpResHttp = 0;
    gUpResText[0] = '\0';
    gUpResultReady = false;
    gUpAbort = false;
    xSemaphoreGive(gUpMutex);

    // Start upload task (a previous one has finished or been told to abort)
    gUpTask = nullptr;
    if (xTaskCreatePinnedToCore(uploadTask, "boardUp", 12288, nullptr, 1, &gUpTask, 0) != pdPASS) {
        return "";
    }

    // Wait for result
    uint32_t waitStart = millis();
    while (millis() - waitStart < timeoutMs) {
        xSemaphoreTake(gUpMutex, portMAX_DELAY);
        bool ready = gUpResultReady;
        uint8_t code = gUpResCode;
        std::string text = gUpResText;
        xSemaphoreGive(gUpMutex);

        if (ready) {
            if (code == 0) return text;
            return "";
        }
        delay(10);
    }

    // Timeout
    gUpAbort = true;
    xSemaphoreTake(gUpMutex, portMAX_DELAY);
    if (gUpSt != Done) {
        gUpResCode = 4;
        snprintf(gUpResText, sizeof(gUpResText), "upload timeout");
        gUpResultReady = true;
        gUpSt = Done;
    }
    xSemaphoreGive(gUpMutex);

    return "";
}

void ping() {
    if (gHavePeer) sendPing();
}

void rediscover() {
    if (gHavePeer) {
        esp_now_del_peer(gPeerMac);
        gHavePeer = false;
    }
    gState.linked = false;
    gState.healthy = false;
    gDiscovering = true;
    gNextHello = millis();
    sendHello();
}

std::string macString() {
    if (!gState.haveMac) return "";
    char buf[18];
    snprintf(buf, sizeof(buf), "%02x:%02x:%02x:%02x:%02x:%02x",
             gState.boardMac[0], gState.boardMac[1], gState.boardMac[2],
             gState.boardMac[3], gState.boardMac[4], gState.boardMac[5]);
    return buf;
}

std::string diag() {
    char b[64];
    snprintf(b, sizeof b, "radio %s, key %s, %lu hello", gNowUp ? "up" : "down", gKeyed ? "ok" : "missing",
             (unsigned long)gHelloSent);
    return b;
}

}  // namespace board
}  // namespace maz
