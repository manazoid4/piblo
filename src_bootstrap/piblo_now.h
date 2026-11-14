// Piblo bootstrap - ESP-NOW link to the M5Stack Cardputer ADV.
//
// Small, fixed message set, no buffers that can grow:
//   PING / PONG / DEVICE_STATUS / DISPLAY_MESSAGE / BUTTON_EVENT
// Every message except PING/PONG carries a 1-byte seq; the peer ACKs the last
// seq it received. ACKs are never acknowledged (that would recurse).
#pragma once
#include <Arduino.h>
#include <esp_now.h>
#include <wifi.h>

namespace pnow {

enum class Kind : uint8_t {
    Ping = 1,
    Pong = 2,
    DeviceStatus = 3,
    DisplayMessage = 4,
    ButtonEvent = 5,
    Ack = 6,          // payload: seq of the message being acknowledged
    PeerHello = 7,    // sent on boot / on explicit pair request
};

constexpr size_t kMaxPayload = 160;   // well under ESP-NOW's 250-byte limit
constexpr uint32_t kHeartbeatMs = 3000;
constexpr uint32_t kPeerLostMs = 12000;
constexpr uint32_t kRetryMs = 700;
constexpr uint8_t kMaxRetries = 3;

struct Message {
    Kind kind;
    uint8_t seq;
    uint8_t flags;
    uint8_t len;
    uint8_t data[kMaxPayload];
};

// Called on the receiving core for every valid inbound frame.
using RxFn = void (*)(const Message& msg, const uint8_t peer[6]);

bool begin(RxFn onRx);
bool addPeer(const uint8_t mac[6], int8_t channel = -1);
void forgetPeer();

// Fire-and-forget. Returns false only if the radio or peer is unavailable.
bool sendRaw(const uint8_t mac[6], const Message& msg);
bool sendToPeer(const Message& msg);

// Reliable send: retries until an ACK arrives or kMaxRetries is hit.
bool sendReliable(const Message& msg);
void update();                       // call every loop; heartbeats + retries
uint8_t nextSeq();

bool peerLinked();
bool peerFresh();                    // ACK seen within kPeerLostMs
int8_t channel();
String peerMac();
uint8_t lastAcked();                 // highest seq the peer confirmed
uint8_t lastSentSeq();
uint32_t sentCount();
uint32_t ackCount();
uint32_t lostCount();

// Convenience builders.
void makePing(Message& m);
void makePong(Message& m, uint8_t ackSeq);
void makeDeviceStatus(Message& m, const char* status);
void makeDisplayMessage(Message& m, const char* text);
void makeButtonEvent(Message& m, uint8_t key, bool pressed);
void makePeerHello(Message& m, const char* name);

// Applies the current Wi-Fi channel to ESP-NOW (called after Wi-Fi connects).
void syncChannel();
void onWiFiEvent(WiFiEvent_t event);

}  // namespace pnow
