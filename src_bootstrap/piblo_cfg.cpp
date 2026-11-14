// Piblo bootstrap - durable settings (NVS). No credential is ever printed.
#include "piblo_cfg.h"

#include <esp_random.h>

namespace cfg {
namespace {
const char* kNs = "piblo";
Preferences prefs;
Settings gCache;
bool gReady = false;

bool open() {
    if (gReady) return true;
    gReady = prefs.begin(kNs, false);
    return gReady;
}

// Reject anything that is not exactly one clean C string.
bool clean(const char* src, size_t cap, char* dst) {
    if (!src) return false;
    size_t n = strnlen(src, cap + 1);
    if (n == 0 || n > cap) return false;
    memcpy(dst, src, n);
    dst[n] = 0;
    return true;
}
}  // namespace

bool load(Settings& out) {
    if (!open()) return false;
    memset(&out, 0, sizeof out);
    String s = prefs.getString("ssid");
    String p = prefs.getString("pass");
    String t = prefs.getString("ota");
    clean(s.c_str(), kSsidMax, out.ssid);
    clean(p.c_str(), kPassMax, out.pass);
    clean(t.c_str(), kTokenMax, out.otaToken);
    size_t n = prefs.getBytesLength("peer");
    if (n == kPeerMax) {
        prefs.getBytes("peer", out.peer, kPeerMax);
        out.hasPeer = true;
    }
    return true;
}

bool saveWifi(const char* ssid, const char* pass) {
    if (!open()) return false;
    char s[kSsidMax + 1], p[kPassMax + 1];
    if (!clean(ssid, kSsidMax, s)) return false;
    if (pass && strlen(pass) > kPassMax) return false;
    memset(p, 0, sizeof p);
    if (pass) { size_t n = strnlen(pass, kPassMax); memcpy(p, pass, n); }
    prefs.putString("ssid", s);
    prefs.putString("pass", p);
    strncpy(gCache.ssid, s, kSsidMax);
    gCache.ssid[kSsidMax] = 0;
    return true;
}

bool savePeer(const uint8_t mac[6]) {
    if (!open() || !mac) return false;
    prefs.putBytes("peer", mac, kPeerMax);
    memcpy(gCache.peer, mac, kPeerMax);
    gCache.hasPeer = true;
    return true;
}

bool clearPeer() {
    if (!open()) return false;
    prefs.remove("peer");
    memset(gCache.peer, 0, kPeerMax);
    gCache.hasPeer = false;
    return true;
}

bool saveOtaToken(const char* token) {
    if (!open()) return false;
    char t[kTokenMax + 1];
    if (!clean(token, kTokenMax, t)) return false;
    prefs.putString("ota", t);
    strncpy(gCache.otaToken, t, kTokenMax);
    gCache.otaToken[kTokenMax] = 0;
    return true;
}

void clearAll() {
    if (!open()) return;
    prefs.clear();
    memset(&gCache, 0, sizeof gCache);
}

bool hasWifi() {
    Settings s;
    if (!load(s)) return false;
    return s.ssid[0] != 0;
}

String otaToken() {
    Settings s;
    if (!load(s)) return String();
    return String(s.otaToken);
}

const uint8_t* peerMac() { return gCache.hasPeer ? gCache.peer : nullptr; }

String ensureOtaToken() {
    if (!open()) return String();
    String t = prefs.getString("ota");
    if (t.length() >= 8 && t.length() <= kTokenMax) return t;
    // 16 random bytes -> 22 base64url chars, URL-safe.
    static const char* kAlpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    char buf[kTokenMax + 1];
    for (int i = 0; i < kTokenMax; ++i) buf[i] = kAlpha[esp_random() % 64];
    buf[kTokenMax] = 0;
    String out = String(buf);
    prefs.putString("ota", out);
    return out;
}

}  // namespace cfg
