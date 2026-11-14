#include "net.h"

#include <M5Unified.h>
#include <WiFi.h>
#include <time.h>

#include <algorithm>

#include "../core/notify.h"
#include "../core/settings.h"
#include "../core/sys.h"

namespace maz {
namespace net {

namespace {
uint32_t gNextRetry    = 0;
uint32_t gBackoff      = 5000;
bool     gWasConnected = false;
bool     gSetupAp      = false;
uint8_t  gFailCycles   = 0;
bool     gTrySecond    = false;  // auto-retry alternates the two saved networks
uint32_t gClockDeadline = 0;     // non-zero while an SNTP answer is awaited


constexpr const char* SETUP_SSID = "Piblo-Setup";
constexpr const char* SETUP_PASS = "piblosetup";
}  // namespace

bool startSetupAp() {
    if (gSetupAp) return true;
    WiFi.mode(WIFI_AP_STA);
    if (!WiFi.softAP(SETUP_SSID, SETUP_PASS)) return false;
    gSetupAp = true;
    Sys.wifiOn = true;
    if (!connected()) {
        Sys.wifiSsid = SETUP_SSID;
        Sys.ip = WiFi.softAPIP().toString().c_str();
    }
    notify::post(Note::Info, "Wi-Fi setup ready", "Piblo-Setup / 192.168.4.1");
    return true;
}

void stopSetupAp() {
    if (!gSetupAp) return;
    WiFi.softAPdisconnect(true);
    gSetupAp = false;
    if (connected()) WiFi.mode(WIFI_STA);
}

bool setupApActive() { return gSetupAp; }
std::string setupIp() { return gSetupAp ? WiFi.softAPIP().toString().c_str() : ""; }

void begin() {
    WiFi.persistent(false);
    Sys.wifiConnected = false;
    Sys.hostAddr      = Cfg.hostAddr;
    Sys.hostPort      = Cfg.hostPort;

    if (Cfg.wifiSsid.empty() && Cfg.wifiSsid2.empty()) {
        startSetupAp();
        return;
    }

    WiFi.mode(WIFI_STA);
    Sys.wifiOn = true;
    const std::string& first = !Cfg.wifiSsid.empty() ? Cfg.wifiSsid : Cfg.wifiSsid2;
    const std::string& pass  = !Cfg.wifiSsid.empty() ? Cfg.wifiPass : Cfg.wifiPass2;
    Sys.wifiSsid = first;
    WiFi.begin(first.c_str(), pass.c_str());
    gNextRetry = millis() + 12000;
}

bool connect(const std::string& ssid, const std::string& pass) {
    if (ssid.empty()) return false;
    WiFi.mode(gSetupAp ? WIFI_AP_STA : WIFI_STA);
    Sys.wifiOn = true;
    WiFi.begin(ssid.c_str(), pass.c_str());

    const uint32_t deadline = millis() + 12000;
    while (millis() < deadline && WiFi.status() != WL_CONNECTED) delay(120);

    const bool ok = WiFi.status() == WL_CONNECTED;
    if (ok) {
        Sys.wifiConnected = true;
        Sys.wifiSsid      = ssid;
        Sys.ip            = WiFi.localIP().toString().c_str();
        gBackoff          = 5000;
        gFailCycles       = 0;
        stopSetupAp();
    }
    return ok;
}

bool connectSaved() {
    if (!Cfg.wifiSsid.empty() && connect(Cfg.wifiSsid, Cfg.wifiPass)) return true;
    if (!Cfg.wifiSsid2.empty() && connect(Cfg.wifiSsid2, Cfg.wifiPass2)) return true;
    return false;
}

bool enable(bool on) {
    if (on) {
        Sys.wifiOn = true;
        if (Cfg.wifiSsid.empty() && Cfg.wifiSsid2.empty()) return startSetupAp();
        WiFi.mode(WIFI_STA);
        if (connectSaved()) return true;
        return startSetupAp();
    }
    stopSetupAp();
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    Sys.wifiOn        = false;
    Sys.wifiConnected = false;
    Sys.hostOnline    = false;
    Sys.ip.clear();
    Sys.wifiSsid.clear();
    return true;
}

void disconnect() {
    WiFi.disconnect();
    Sys.wifiConnected = false;
    Sys.hostOnline    = false;
    Sys.ip             = gSetupAp ? setupIp() : "";
    if (gSetupAp) Sys.wifiSsid = SETUP_SSID;
}

bool connected() { return WiFi.status() == WL_CONNECTED; }
int rssi() { return connected() ? WiFi.RSSI() : 0; }
std::string ip() { return connected() ? WiFi.localIP().toString().c_str() : setupIp(); }

void update() {
    if (!Sys.wifiOn) return;

    const bool now = connected();
    Sys.wifiConnected = now;
    if (now) {
        Sys.ip = WiFi.localIP().toString().c_str();
        Sys.wifiSsid = WiFi.SSID().c_str();
        if (!gWasConnected) {
            notify::post(Note::Success, "Wi-Fi connected", Sys.wifiSsid + " / piblo.local");
            // Async: the old blocking syncClock() froze the UI loop for up to 6 s on every (re)connect.
            configTime(Cfg.tzMinutesOffset * 15 * 60, 0, "pool.ntp.org", "time.nist.gov");
            gClockDeadline = millis() + 15000;
        }
        gWasConnected = true;
        gBackoff = 5000;
        gFailCycles = 0;
        if (gSetupAp) stopSetupAp();
        if (gClockDeadline) {
            const time_t t = time(nullptr);
            if (t > 1700000000) {
                Sys.timeValid = true;
                Cfg.lastKnownEpoch = static_cast<uint32_t>(t);
                Cfg.save();
                gClockDeadline = 0;
            } else if (static_cast<int32_t>(millis() - gClockDeadline) >= 0) {
                gClockDeadline = 0;
            }
        }
        return;
    }

    if (gSetupAp) {
        Sys.ip = setupIp();
        Sys.wifiSsid = SETUP_SSID;
    }

    if (gWasConnected) {
        notify::post(Note::Warn, "Wi-Fi lost", "retrying / setup AP if needed");
        gWasConnected = false;
        Sys.hostOnline = false;
        gNextRetry = millis() + gBackoff;
    }

    if (Cfg.wifiSsid.empty() && Cfg.wifiSsid2.empty()) {
        if (!gSetupAp) startSetupAp();
        return;
    }

    if (millis() >= gNextRetry) {
        // Non-blocking: connect() polled for up to 12 s per SSID (24 s with two) on the UI loop each
        // retry. WiFi.begin() returns at once; success is picked up by the connected() check above.
        const bool two = !Cfg.wifiSsid.empty() && !Cfg.wifiSsid2.empty();
        const bool useSecond = Cfg.wifiSsid.empty() || (two && gTrySecond);
        gTrySecond = two && !gTrySecond;
        const std::string& ssid = useSecond ? Cfg.wifiSsid2 : Cfg.wifiSsid;
        const std::string& pass = useSecond ? Cfg.wifiPass2 : Cfg.wifiPass;
        WiFi.mode(gSetupAp ? WIFI_AP_STA : WIFI_STA);
        WiFi.begin(ssid.c_str(), pass.c_str());
        gFailCycles++;
        gNextRetry = millis() + std::max<uint32_t>(gBackoff, 12000);
        gBackoff = gBackoff < 60000 ? std::min<uint32_t>(60000, gBackoff * 2) : 60000;
        if (gFailCycles >= 2 && !gSetupAp) startSetupAp();
    }
}

std::vector<Ap> scan(uint32_t timeoutMs) {
    std::vector<Ap> out;
    WiFi.mode(gSetupAp ? WIFI_AP_STA : WIFI_STA);
    Sys.wifiOn = true;

    const int n = WiFi.scanNetworks(false, true, false, timeoutMs / 10);
    for (int i = 0; i < n && i < 24; ++i) {
        Ap a;
        a.ssid  = WiFi.SSID(i).c_str();
        a.rssi  = WiFi.RSSI(i);
        a.open  = WiFi.encryptionType(i) == WIFI_AUTH_OPEN;
        a.known = (a.ssid == Cfg.wifiSsid) || (a.ssid == Cfg.wifiSsid2);
        if (!a.ssid.empty()) out.push_back(a);
    }
    WiFi.scanDelete();
    std::sort(out.begin(), out.end(), [](const Ap& a, const Ap& b) { return a.rssi > b.rssi; });
    return out;
}

bool probeHost() {
    Sys.hostAddr = Cfg.hostAddr;
    Sys.hostPort = Cfg.hostPort;
    if (Cfg.hostAddr.empty() || !connected()) {
        Sys.hostOnline = false;
        return false;
    }
    WiFiClient c;
    c.setTimeout(2);
    const bool ok = c.connect(Cfg.hostAddr.c_str(), Cfg.hostPort);
    c.stop();
    Sys.hostOnline = ok;
    return ok;
}

bool syncClock() {
    if (!connected()) return false;
    configTime(Cfg.tzMinutesOffset * 15 * 60, 0, "pool.ntp.org", "time.nist.gov");
    const uint32_t deadline = millis() + 6000;
    while (millis() < deadline) {
        const time_t t = time(nullptr);
        if (t > 1700000000) {
            Sys.timeValid = true;
            Cfg.lastKnownEpoch = static_cast<uint32_t>(t);
            Cfg.save();
            return true;
        }
        delay(150);
    }
    return false;
}

}  // namespace net
}  // namespace maz
