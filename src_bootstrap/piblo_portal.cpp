// Piblo bootstrap - HTTP portal: Wi-Fi setup + authenticated OTA.
#include "piblo_portal.h"

#include <Arduino.h>
#include <ArduinoOTA.h>
#include <esp_ota_ops.h>
#include <HTTPClient.h>
#include <HTTPUpdate.h>
#include <Update.h>
#include <WiFi.h>
#include <WiFiClient.h>

#include "piblo_cfg.h"
#include "piblo_screen.h"
#include "piblo_ver.h"

namespace portal {
namespace {
const char* kTag = "portal";
WiFiServer gServer(80);
WiFiClient gClient;
bool gActive = false;
bool gUpdating = false;
String gToken;
String gStatus = "starting";
uint32_t gRestartAt = 0;

void setStatus(const char* s) { gStatus = s; }

String html(const String& title, const String& body) {
    String h = F("<!doctype html><html><head><meta name=viewport content=\"width=device-width,initial-scale=1\">"
                 "<meta name=robots content=noindex><title>Piblo</title><style>"
                 "body{margin:0;background:#0b0e12;color:#eef2f5;font:15px system-ui,-apple-system,Segoe UI,sans-serif}"
                 "main{max-width:520px;margin:auto;padding:20px 16px 60px}"
                 "h1{font-size:24px;margin:0 0 4px} p{color:#9aa7b2;line-height:1.5}"
                 "label{display:block;margin:14px 0 6px;color:#9aa7b2;font-size:13px}"
                 "input,button{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #2a3440;"
                 "background:#121821;color:#eef2f5;font:inherit;margin-bottom:8px}"
                 "button{background:#ff7a18;border-color:#ff7a18;color:#150a00;font-weight:700;cursor:pointer}"
                 ".ghost{background:#121821;color:#eef2f5;border-color:#2a3440}"
                 ".row{display:flex;gap:8px}.row>*{flex:1}"
                 ".card{border:1px solid #2a3440;border-radius:12px;padding:14px;margin:14px 0;background:#121821}"
                 ".ok{color:#42d58a}.bad{color:#ff676d}.mono{font-family:ui-monospace,Consolas,monospace;font-size:13px;word-break:break-all}"
                 ".note{color:#9aa7b2;font-size:13px}"
                 "</style></head><body><main>");
    h += F("<h1>PIBLO</h1><p class=note>");
    h += title;
    h += F("</p>");
    h += body;
    h += F("</main></body></html>");
    return h;
}

void send(WiFiClient& c, int code, const String& type, const String& body) {
    c.printf("HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %u\r\n"
             "Connection: close\r\nCache-Control: no-store\r\n\r\n",
             code, code == 200 ? "OK" : (code == 401 ? "Unauthorized" : "Error"),
             type.c_str(), (unsigned)body.length());
    c.print(body);
}

bool authorized(const String& req) {
    if (gToken.length() < 8) return false;   // no token provisioned yet -> refuse
    int i = req.indexOf("X-Piblo-Token:");
    if (i < 0) return false;
    int e = req.indexOf("\r\n", i);
    String t = req.substring(i + 13, e < 0 ? req.length() : e);
    t.trim();
    return t == gToken;
}

// Reads one request head into `req`, returns body length or -1.
int readHead(WiFiClient& c, String& req, uint32_t timeoutMs) {
    req = "";
    uint32_t start = millis();
    while (c.connected() && (int32_t)(millis() - start) < (int32_t)timeoutMs) {
        while (c.available()) {
            char ch = (char)c.read();
            req += ch;
            if (req.endsWith("\r\n\r\n")) {
                int cl = req.indexOf("Content-Length:");
                if (cl < 0) return 0;
                int e = req.indexOf("\r\n", cl);
                return req.substring(cl + 15, e).toInt();
            }
            if (req.length() > 8192) return -1;
        }
        delay(1);
    }
    return -1;
}

String formValue(const String& body, const char* key) {
    String pat = String(key) + "=";
    int i = body.indexOf(pat);
    if (i < 0) return String();
    i += pat.length();
    int e = body.indexOf('&', i);
    String v = body.substring(i, e < 0 ? body.length() : e);
    // minimal percent-decode
    String out;
    for (size_t j = 0; j < v.length(); ++j) {
        char ch = v[j];
        if (ch == '+') out += ' ';
        else if (ch == '%' && j + 2 < v.length()) {
            auto hex = [](char c) -> int {
                if (c >= '0' && c <= '9') return c - '0';
                if (c >= 'a' && c <= 'f') return c - 'a' + 10;
                if (c >= 'A' && c <= 'F') return c - 'A' + 10;
                return -1;
            };
            int a = hex(v[j + 1]), b = hex(v[j + 2]);
            if (a >= 0 && b >= 0) { out += (char)((a << 4) | b); j += 2; }
        } else out += ch;
    }
    return out;
}

void handleRoot(WiFiClient& c, bool auth) {
    cfg::Settings s;
    cfg::load(s);
    String body;
    body += F("<div class=card><div class=mono>Wi-Fi: ");
    body += (WiFi.SSID().length() ? WiFi.SSID() : "setup AP");
    body += "<br>IP: ";
    body += WiFi.localIP().toString();
    body += "<br>Firmware: " PIBLO_BOOT_VERSION "</div>";
    body += F("<form method=POST action=/save>"
              "<label>Wi-Fi name (2.4 GHz)</label><input name=ssid maxlength=32 required>"
              "<label>Password</label><input name=pass type=password maxlength=63>"
              "<button>CONNECT</button></form>");
    if (auth) {
        body += F("<p class=note>OTA token (shown once, stored on device): <span class=mono>");
        body += gToken;
        body += F("</span></p>");
        body += F("<div class=row><a href=/do-update><button class=ghost>UPLOAD FIRMWARE</button></a>"
                  "<a href=/pair><button class=ghost>FIND CARDPUTER</button></a></div>");
        body += F("<p class=note>Status: <span class=mono>");
        body += gStatus;
        body += F("</span></p>");
    } else {
        body += F("<p class=note>Unlock with the OTA token for firmware updates.</p>"
                  "<form method=POST action=/login><label>Token</label><input name=token maxlength=32>"
                  "<button>UNLOCK</button></form>");
    }
    send(c, 200, "text/html; charset=utf-8", html("Set up this Piblo board.", body));
}

void handleSave(WiFiClient& c, const String& body) {
    String ssid = formValue(body, "ssid");
    String pass = formValue(body, "pass");
    if (ssid.length() < 1 || ssid.length() > 32 || pass.length() > 63) {
        send(c, 400, "text/html", html("Connection", "<p class=bad>Invalid Wi-Fi details.</p>"));
        return;
    }
    cfg::saveWifi(ssid.c_str(), pass.c_str());
    setStatus("saving Wi-Fi");
    send(c, 200, "text/html", html("Saved",
        "<p class=ok>Saved. The board is joining <span class=mono>" + ssid +
        "</span>. It will show its IP on the screen.</p>"
        "<p class=note>Give it 10 seconds. If it cannot join, it comes back as the "
        "<span class=mono>Piblo-Setup</span> hotspot.</p>"));
}

void handleLogin(WiFiClient& c, const String& body) {
    String t = formValue(body, "token");
    if (t == gToken) { c.stop(); gClient.stop(); handleRoot(c, true); return; }
    send(c, 401, "text/html", html("Unlock", "<p class=bad>Wrong token.</p>"));
}

// Streams a multipart upload into the OTA slot with a hard size cap.
// `firmwareStart` returns true once the file's 0xE9 magic has been seen.
void handleDoUpdate(WiFiClient& c, const String& req, int contentLength) {
    if (!authorized(req)) { send(c, 401, "text/plain", "unauthorized"); return; }
    if (contentLength <= 0 || contentLength > 5 * 1024 * 1024) {
        send(c, 400, "text/plain", "bad length");
        return;
    }
    const esp_partition_t* part = esp_ota_get_next_update_partition(nullptr);
    if (!part) { send(c, 500, "text/plain", "no slot"); return; }
    if ((size_t)contentLength > part->size) { send(c, 400, "text/plain", "too big"); return; }

    if (!Update.begin(part->size, U_FLASH)) {
        send(c, 500, "text/plain", "begin failed");
        return;
    }
    gUpdating = true;
    setStatus("flashing");
    // Drain the request head, then read the multipart body. We only need the
    // file bytes, so skip to the first 0xE9 and stream from there.
    uint8_t buf[1024];
    size_t got = 0, written = 0, total = 0;
    bool started = false;
    uint32_t last = millis();
    while (c.connected() && total < (size_t)contentLength && (int32_t)(millis() - last) < 20000) {
        int avail = c.available();
        if (avail <= 0) { delay(1); continue; }
        int n = c.read(buf, (size_t)min((int)sizeof buf, avail));
        if (n <= 0) continue;
        total += (size_t)n;
        last = millis();
        for (int i = 0; i < n; ++i) {
            if (!started) {
                if (buf[i] == 0xE9) started = true;
                else continue;
            }
            // Once started, every byte is firmware (header magic included).
            buf[got++] = buf[i];
            if (got == sizeof buf) {
                if (Update.write(buf, got) != got) { Update.abort(); goto fail; }
                written += got;
                got = 0;
            }
        }
    }
    if (got) {
        if (Update.write(buf, got) != got) { Update.abort(); goto fail; }
        written += got;
    }
    if (!started || written < 65536) {
        Update.abort();
        setStatus("rejected image");
        send(c, 400, "text/plain", "not an ESP32 image");
        gUpdating = false;
        return;
    }
    if (!Update.end(true)) {
        setStatus("verify failed");
        send(c, 500, "text/plain", "verify failed");
        gUpdating = false;
        return;
    }
    if (esp_ota_set_boot_partition(part) != ESP_OK) {
        setStatus("set boot failed");
        send(c, 500, "text/plain", "set boot failed");
        gUpdating = false;
        return;
    }
    setStatus("update OK, rebooting");
    send(c, 200, "text/html", html("Update", "<p class=ok>Firmware accepted. Rebooting...</p>"));
    gRestartAt = millis() + 900;
    return;
fail:
    setStatus("write failed");
    send(c, 500, "text/plain", "write failed");
    gUpdating = false;
}

void handlePair(WiFiClient& c) {
    send(c, 200, "text/html", html("Pair",
        "<p class=note>Pairing uses ESP-NOW. Put the Cardputer ADV on the same "
        "2.4 GHz network, hold its pairing key, then reload this page.</p>"
        "<p class=mono>Status: " + String(gStatus) + "</p>"));
}

void serve() {
    if (!gClient || !gClient.connected()) {
        gClient = gServer.available();
        if (!gClient) return;
    }
    String req;
    int len = readHead(gClient, req, 4000);
    if (req.length() == 0) { gClient.stop(); return; }
    if (len < 0) { gClient.stop(); return; }

    String method = req.substring(0, req.indexOf(' '));
    int p = req.indexOf(' ') + 1;
    String path = req.substring(p, req.indexOf(' ', p));
    bool auth = authorized(req);
    Serial.printf("portal: %s %s auth=%d len=%d\n", method.c_str(), path.c_str(), auth ? 1 : 0, len);

    String body;
    if (len > 0) {
        size_t need = (size_t)len;
        while (body.length() < need && gClient.connected()) {
            while (gClient.available() && body.length() < need) body += (char)gClient.read();
            if (body.length() < need) delay(1);
        }
    }

    if (path == "/" || path == "/index.html") handleRoot(gClient, auth);
    else if (path == "/save" && method == "POST") handleSave(gClient, body);
    else if (path == "/login" && method == "POST") handleLogin(gClient, body);
    else if (path == "/do-update" && method == "POST") handleDoUpdate(gClient, req, len);
    else if (path == "/pair") handlePair(gClient);
    else if (path == "/screen") shot::serve(gClient);
    else send(gClient, 404, "text/plain", "not found");
    gClient.stop();
}
}  // namespace

void begin() {
    gToken = cfg::ensureOtaToken();
    gServer.begin();
    gActive = true;
    setStatus("portal ready");
}

void update() {
    if (!gActive) return;
    serve();
    if (gRestartAt && (int32_t)(millis() - gRestartAt) >= 0) {
        Serial.println("portal: rebooting into new firmware");
        ESP.restart();
    }
}

bool active() { return gActive; }
String ip() { return WiFi.localIP().toString(); }
String otaToken() { return gToken; }
bool updating() { return gUpdating; }
const char* lastStatus() { return gStatus.c_str(); }

}  // namespace portal
