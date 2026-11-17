// Piblo — persisted settings.
#pragma once
#include <stdint.h>

#include <string>

// Display label only; the parseable version stays NOD_FW_VERSION (VERSION file).
#define PIBLO_LABEL "v1.5 beta"

namespace maz {

constexpr uint8_t TALK_ROUTE_COUNT = 4;
constexpr uint8_t TALK_ROUTE_LOCAL = 0;
constexpr uint8_t TALK_ROUTE_AUTO = 1;
constexpr uint8_t TALK_ROUTE_CLOUD = 2;
constexpr uint8_t TALK_ROUTE_MAZLATEST = 3;

inline uint8_t normalizedTalkRoute(uint8_t route) {
    return route < TALK_ROUTE_COUNT ? route : 2;
}

inline const char* talkRouteApiName(uint8_t route) {
    switch (normalizedTalkRoute(route)) {
        case 0: return "local";
        case 1: return "auto";
        case 3: return "mazlatest";
        default: return "cloud";
    }
}

inline uint8_t talkRouteFromApiName(const std::string& route, uint8_t fallback = TALK_ROUTE_CLOUD) {
    if (route == "local" || route == "0") return TALK_ROUTE_LOCAL;
    if (route == "auto" || route == "1") return TALK_ROUTE_AUTO;
    if (route == "cloud" || route == "2") return TALK_ROUTE_CLOUD;
    if (route == "mazlatest" || route == "3") return TALK_ROUTE_MAZLATEST;
    return normalizedTalkRoute(fallback);
}

inline const char* talkRouteLabel(uint8_t route) {
    switch (normalizedTalkRoute(route)) {
        case 0: return "LOCAL";
        case 1: return "AUTO";
        case 3: return "MAZLATEST";
        default: return "CLOUD";
    }
}

struct Settings {
    uint8_t  brightness    = 110;
    uint8_t  volume        = 220;  // soft limiter (audio/limiter.h) keeps this clean
    bool     uiSounds      = true;
    // Quiet mode: last choice only. The device always boots Normal (see quiet.cpp).
    uint8_t  quietLevel    = 0;
    uint8_t  quietDur      = 1;
    uint16_t screenTimeout = 60;
    uint8_t  micGain       = 12;
    bool     preferSd      = true;

    std::string wifiSsid, wifiPass;
    std::string wifiSsid2, wifiPass2;

    std::string hostAddr;
    uint16_t    hostPort = 8787;
    std::string hostRemoteUrl;
    std::string hostToken;
    // Persist the public symbolic identity ("mazlatest", "cloud", etc.). The
    // byte remains an internal compact enum and supports legacy NVS migration.
    uint8_t     talkRoute = TALK_ROUTE_MAZLATEST;
    bool        ttsEnabled = true;
    uint8_t     nudgePollMinutes = 5;
    bool        firstRunComplete = false;
    // Easy Mode: beginner home, plain words, no Fn needed. On by default so a
    // first-time user gets it; the classic home is one toggle away.
    bool        easyMode = true;
    bool        autoUpdate = false;  // install staged firmware by itself while charging and idle

    // Four quick keys are stable action IDs from a fixed list; they are never
    // shell commands. CALL remains quick slot 1 by default.
    bool        fieldMode = false;
    std::string quick1 = "talk";
    std::string quick2 = "braindump";
    std::string quick3 = "laptop";
    std::string quick4 = "beam";

    int8_t   tzMinutesOffset = 0;
    uint32_t lastKnownEpoch  = 0;

    void load();
    void save() const;
    void applyToHardware() const;
};

extern Settings Cfg;

}  // namespace maz
