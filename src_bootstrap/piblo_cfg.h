// Piblo bootstrap - small durable settings store.
//
// Credentials live in NVS ("piblo" namespace) on the device only. Nothing here
// is compiled into the image, and the value is never printed. Values are
// fixed-size so a corrupt/truncated blob is rejected instead of half-applied.
#pragma once
#include <Arduino.h>
#include <Preferences.h>

namespace cfg {

constexpr size_t kSsidMax = 32;
constexpr size_t kPassMax = 63;
constexpr size_t kPeerMax = 6;
constexpr size_t kTokenMax = 32;

struct Settings {
    char ssid[kSsidMax + 1] = "";
    char pass[kPassMax + 1] = "";
    uint8_t peer[kPeerMax] = {0};
    bool hasPeer = false;
    char otaToken[kTokenMax + 1] = "";
};

bool load(Settings& out);
bool saveWifi(const char* ssid, const char* pass);
bool savePeer(const uint8_t mac[6]);
bool clearPeer();
bool saveOtaToken(const char* token);
void clearAll();                 // factory reset of this namespace

bool hasWifi();
String otaToken();               // for the HTTP layer only
const uint8_t* peerMac();

// Generates a random OTA token on first use. Never logged.
String ensureOtaToken();

}  // namespace cfg
