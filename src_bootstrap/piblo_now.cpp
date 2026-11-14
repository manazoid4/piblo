// Piblo bootstrap - ESP-NOW link to the M5Stack Cardputer ADV.
#include "piblo_now.h"

#include <esp_log.h>
#include <esp_wifi.h>
#include <string.h>

namespace pnow {
namespace {
const char* kTag = "pnow";

uint8_t gPeer[6] = {0};
bool gPeerSet = false;
RxFn gRx = nullptr;
uint8_t gSeq = 0;
uint8_t gLastAck = 0;
int8_t gChannel = 0;
bool gLinked = false;
uint32_t gLastPeerMs = 0;
uint32_t gLastBeat = 0;

// Retry window for the message currently awaiting an ACK.
bool gPending = false;
uint8_t gPendingSeq = 0;
uint8_t gRetries = 0;
uint32_t gNextRetry = 0;
Message gPendingMsg;

uint32_t gSent = 0, gAck = 0, gLost = 0;

bool peerIsBroadcast() {
    const uint8_t bc[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    return memcmp(gPeer, bc, 6) == 0;
}

void noteAck(uint8_t seq) {
    if (gPending && seq == gPendingSeq) {
        gPending = false;
        ++gRetries;
        ++gAck;
    }
    gLastAck = seq;
    gLinked = true;
    gLastPeerMs = millis();
}

void onReceive(const uint8_t* mac, const uint8_t* data, int len) {
    if (!mac || !data || len < (int)sizeof(Message)) return;
    Message msg;
    memcpy(&msg, data, sizeof(Message));
    if ((uint8_t)msg.kind == (uint8_t)Kind::Ack) {
        noteAck(msg.seq);
        return;
    }
    if (gRx) gRx(msg, mac);
    // ACK everything else so the peer can stop retrying.
    Message ack;
    memset(&ack, 0, sizeof ack);
    ack.kind = Kind::Ack;
    ack.seq = msg.seq;
    esp_now_send(mac, (const uint8_t*)&ack, sizeof ack);
    gLastPeerMs = millis();
    gLinked = true;
}

void onSend(const uint8_t* mac, esp_now_send_status_t status) {
    if (mac && memcmp(mac, gPeer, 6) == 0) {
        if (status == ESP_NOW_SEND_SUCCESS) ++gSent;
        else ++gLost;
    }
}
}  // namespace

uint8_t nextSeq() { return ++gSeq; }

bool sendRaw(const uint8_t mac[6], const Message& msg) {
    if (!mac) return false;
    return esp_now_send(mac, (const uint8_t*)&msg, sizeof msg) == ESP_OK;
}

bool sendToPeer(const Message& msg) {
    if (!gPeerSet) return false;
    return sendRaw(gPeer, msg);
}

bool sendReliable(const Message& msg) {
    if (!gPeerSet) return false;
    if (gPending) return false;          // one in flight; caller can retry later
    gPendingMsg = msg;
    gPendingSeq = msg.seq;
    gRetries = 0;
    gPending = true;
    gNextRetry = millis();
    return sendRaw(gPeer, gPendingMsg);
}

void update() {
    const uint32_t now = millis();
    if (gPending) {
        if (now - gNextRetry >= kRetryMs && gRetries < kMaxRetries) {
            ++gRetries;
            gNextRetry = now;
            sendRaw(gPeer, gPendingMsg);
        }
        if (gRetries >= kMaxRetries) {
            gPending = false;
            ++gLost;
        }
    }
    if (gPeerSet && now - gLastBeat >= kHeartbeatMs) {
        gLastBeat = now;
        Message beat;
        makePing(beat);
        sendRaw(gPeer, beat);
    }
    if (gLinked && now - gLastPeerMs > kPeerLostMs) gLinked = false;
}

bool peerLinked() { return gPeerSet && gLinked; }
bool peerFresh() { return gPeerSet && (millis() - gLastPeerMs) < kPeerLostMs; }
int8_t channel() { return gChannel; }
String peerMac() {
    if (!gPeerSet) return String();
    char buf[18];
    snprintf(buf, sizeof buf, "%02x:%02x:%02x:%02x:%02x:%02x",
             gPeer[0], gPeer[1], gPeer[2], gPeer[3], gPeer[4], gPeer[5]);
    return String(buf);
}
uint8_t lastAcked() { return gLastAck; }
uint8_t lastSentSeq() { return gSeq; }
uint32_t sentCount() { return gSent; }
uint32_t ackCount() { return gAck; }
uint32_t lostCount() { return gLost; }

void makePing(Message& m) { memset(&m, 0, sizeof m); m.kind = Kind::Ping; m.seq = nextSeq(); }
void makePong(Message& m, uint8_t ackSeq) {
    memset(&m, 0, sizeof m);
    m.kind = Kind::Pong;
    m.seq = nextSeq();
    m.data[0] = ackSeq;
    m.len = 1;
}
void makeDeviceStatus(Message& m, const char* status) {
    memset(&m, 0, sizeof m);
    m.kind = Kind::DeviceStatus;
    m.seq = nextSeq();
    if (status) {
        m.len = (uint8_t)min(strlen(status), kMaxPayload);
        memcpy(m.data, status, m.len);
    }
}
void makeDisplayMessage(Message& m, const char* text) {
    memset(&m, 0, sizeof m);
    m.kind = Kind::DisplayMessage;
    m.seq = nextSeq();
    if (text) {
        m.len = (uint8_t)min(strlen(text), kMaxPayload);
        memcpy(m.data, text, m.len);
    }
}
void makeButtonEvent(Message& m, uint8_t key, bool pressed) {
    memset(&m, 0, sizeof m);
    m.kind = Kind::ButtonEvent;
    m.seq = nextSeq();
    m.data[0] = key;
    m.data[1] = pressed ? 1 : 0;
    m.len = 2;
}
void makePeerHello(Message& m, const char* name) {
    memset(&m, 0, sizeof m);
    m.kind = Kind::PeerHello;
    m.seq = nextSeq();
    if (name) {
        m.len = (uint8_t)min(strlen(name), kMaxPayload);
        memcpy(m.data, name, m.len);
    }
}

void syncChannel() {
    uint8_t primary = 0;
    wifi_second_chan_t second = WIFI_SECOND_CHAN_NONE;
    if (esp_wifi_get_channel(&primary, &second) == ESP_OK) gChannel = (int8_t)primary;
}

void onWiFiEvent(WiFiEvent_t event) {
    if (event == ARDUINO_EVENT_WIFI_STA_CONNECTED || event == ARDUINO_EVENT_WIFI_AP_START) syncChannel();
}

bool begin(RxFn onRx) {
    gRx = onRx;
    if (esp_now_init() != ESP_OK) {
        ESP_LOGW(kTag, "esp_now_init failed");
        return false;
    }
    esp_now_register_recv_cb(onReceive);
    esp_now_register_send_cb(onSend);
    WiFi.onEvent(onWiFiEvent, ARDUINO_EVENT_WIFI_STA_CONNECTED);
    WiFi.onEvent(onWiFiEvent, ARDUINO_EVENT_WIFI_AP_START);
    syncChannel();
    return true;
}

bool addPeer(const uint8_t mac[6], int8_t ch) {
    if (!mac) return false;
    esp_now_peer_info_t info;
    memset(&info, 0, sizeof info);
    memcpy(info.peer_addr, mac, 6);
    info.ifidx = WIFI_IF_STA;
    if (ch > 0) {
        info.channel = (uint8_t)ch;
    } else {
        info.channel = (gChannel > 0) ? (uint8_t)gChannel : 0;  // 0 = follow station channel
    }
    info.encrypt = false;
    esp_err_t err = esp_now_add_peer(&info);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "add_peer %d", (int)err);
        return false;
    }
    memcpy(gPeer, mac, 6);
    gPeerSet = true;
    gLinked = false;
    gLastPeerMs = 0;
    gPending = false;
    return true;
}

void forgetPeer() {
    if (gPeerSet) esp_now_del_peer(gPeer);
    gPeerSet = false;
    gLinked = false;
    gPending = false;
}

}  // namespace pnow
