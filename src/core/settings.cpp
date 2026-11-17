#include "settings.h"

#include <M5Unified.h>
#include <Preferences.h>

#include "sys.h"

namespace maz {

Settings Cfg;
SysState Sys;

namespace {
constexpr const char* NS = "mazpocket";  // storage key, never shown: renaming it would wipe saved Wi-Fi and pairing
Preferences prefs;
}  // namespace

void Settings::load() {
    if (!prefs.begin(NS, /*readOnly=*/true)) {
        ESP_LOGI("cfg", "no saved settings, using defaults");
        applyToHardware();
        return;
    }
    brightness       = prefs.getUChar("bright", brightness);
    volume           = prefs.getUChar("vol", volume);
    uiSounds         = prefs.getBool("snd", uiSounds);
    quietLevel       = prefs.getUChar("qlvl", quietLevel);
    quietDur         = prefs.getUChar("qdur", quietDur);
    screenTimeout    = prefs.getUShort("stimeout", screenTimeout);
    micGain          = prefs.getUChar("mgain", micGain);
    preferSd         = prefs.getBool("prefsd", preferSd);
    wifiSsid         = prefs.getString("ssid", "").c_str();
    wifiPass         = prefs.getString("pass", "").c_str();
    wifiSsid2        = prefs.getString("ssid2", "").c_str();
    wifiPass2        = prefs.getString("pass2", "").c_str();
    hostAddr         = prefs.getString("host", "").c_str();
    hostPort         = prefs.getUShort("hostport", hostPort);
    hostRemoteUrl    = prefs.getString("hremote", "").c_str();
    hostToken        = prefs.getString("htoken", "").c_str();
    const uint8_t legacyTalkRoute = normalizedTalkRoute(prefs.getUChar("route", talkRoute));
    const std::string savedTalkRoute = prefs.getString("route_id", "").c_str();
    talkRoute        = talkRouteFromApiName(savedTalkRoute, legacyTalkRoute);
    ttsEnabled       = prefs.getBool("tts", ttsEnabled);
    nudgePollMinutes = prefs.getUChar("npoll", nudgePollMinutes);
    firstRunComplete = prefs.getBool("firstrun", firstRunComplete);
    easyMode         = prefs.getBool("easy", easyMode);
    autoUpdate       = prefs.getBool("autoupd", autoUpdate);
    fieldMode        = prefs.getBool("field", fieldMode);
    quick1           = prefs.getString("q1", quick1.c_str()).c_str();
    quick2           = prefs.getString("q2", quick2.c_str()).c_str();
    quick3           = prefs.getString("q3", quick3.c_str()).c_str();
    quick4           = prefs.getString("q4", quick4.c_str()).c_str();
    tzMinutesOffset  = prefs.getChar("tz", tzMinutesOffset);
    lastKnownEpoch   = prefs.getULong("epoch", 0);
    prefs.end();
    applyToHardware();
}

void Settings::save() const {
    if (!prefs.begin(NS, /*readOnly=*/false)) {
        ESP_LOGE("cfg", "NVS open failed, settings not saved");
        return;
    }
    prefs.putUChar("bright", brightness);
    prefs.putUChar("vol", volume);
    prefs.putBool("snd", uiSounds);
    prefs.putUChar("qlvl", quietLevel);
    prefs.putUChar("qdur", quietDur);
    prefs.putUShort("stimeout", screenTimeout);
    prefs.putUChar("mgain", micGain);
    prefs.putBool("prefsd", preferSd);
    prefs.putString("ssid", wifiSsid.c_str());
    prefs.putString("pass", wifiPass.c_str());
    prefs.putString("ssid2", wifiSsid2.c_str());
    prefs.putString("pass2", wifiPass2.c_str());
    prefs.putString("host", hostAddr.c_str());
    prefs.putUShort("hostport", hostPort);
    prefs.putString("hremote", hostRemoteUrl.c_str());
    prefs.putString("htoken", hostToken.c_str());
    // Symbolic identity is authoritative. Keep the byte during the migration
    // window so older firmware can still read the last compatible selection.
    prefs.putString("route_id", talkRouteApiName(talkRoute));
    prefs.putUChar("route", talkRoute);
    prefs.putBool("tts", ttsEnabled);
    prefs.putUChar("npoll", nudgePollMinutes);
    prefs.putBool("firstrun", firstRunComplete);
    prefs.putBool("easy", easyMode);
    prefs.putBool("autoupd", autoUpdate);
    prefs.putBool("field", fieldMode);
    prefs.putString("q1", quick1.c_str());
    prefs.putString("q2", quick2.c_str());
    prefs.putString("q3", quick3.c_str());
    prefs.putString("q4", quick4.c_str());
    prefs.putChar("tz", tzMinutesOffset);
    prefs.putULong("epoch", lastKnownEpoch);
    prefs.end();
}

void Settings::applyToHardware() const {
    M5.Display.setBrightness(brightness < 8 ? 8 : brightness);
    M5.Speaker.setVolume(volume);
}

uint32_t SysState::uptimeSeconds() const {
    return (millis() - bootMillis) / 1000;
}

}  // namespace maz
