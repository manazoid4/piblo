#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiUdp.h>
#include <mbedtls/sha256.h>
#include <pair_connect.h>  // firmware/nod/include (pure pairing logic)

#include <string>

#include "../core/settings.h"
#include "../core/sys.h"
#include "../net/mazhost.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

std::string tokenId() {
    if (Cfg.hostToken.empty()) return "UNPAIRED";
    unsigned char digest[32] = {};
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    if (mbedtls_sha256_starts_ret(&ctx, 0) != 0 ||
        mbedtls_sha256_update_ret(&ctx,
            reinterpret_cast<const unsigned char*>(Cfg.hostToken.data()),
            Cfg.hostToken.size()) != 0 ||
        mbedtls_sha256_finish_ret(&ctx, digest) != 0) {
        mbedtls_sha256_free(&ctx);
        return "ERROR";
    }
    mbedtls_sha256_free(&ctx);
    static const char H[] = "0123456789abcdef";
    char out[13];
    for (int i = 0; i < 6; ++i) {
        out[i * 2] = H[(digest[i] >> 4) & 0xF];
        out[i * 2 + 1] = H[digest[i] & 0xF];
    }
    out[12] = 0;
    return out;
}

// ---- "Connect to computer": discovery + code/Allow (docs/protocol/DEVICE-PAIRING.md) ----
// One short-lived task does the slow network work so the UI loop never waits on it. It only
// writes this struct (fields first, phase last); the UI thread reads it and saves the config.
struct PcLink {
    volatile nod::pair::Phase phase = nod::pair::Phase::Discovering;
    volatile uint32_t gen = 0;      // bumped to cancel the running task
    volatile bool running = false;  // task alive
    std::string ip, name, code, token;
    uint16_t port = 0;
};
PcLink gLink;

constexpr uint16_t kDiscoveryPort = 8788;

bool cancelled(uint32_t myGen) { return gLink.gen != myGen; }

void setPhase(nod::pair::Phase p) {
    __sync_synchronize();
    gLink.phase = p;
}

void resetLink() {
    gLink.ip.clear();
    gLink.name.clear();
    gLink.code.clear();
    gLink.token.clear();
    gLink.port = 0;
    gLink.phase = nod::pair::Phase::Discovering;
}

// Broadcast the discover line up to 3 times, 400 ms apart; take the first valid reply.
bool discover(uint32_t myGen, nod::pair::Discovery& found, std::string& ip) {
    WiFiUDP udp;
    if (!udp.begin(kDiscoveryPort + 1)) return false;
    static const char kLine[] = "PIBLO-DISCOVER 1\n";
    for (int attempt = 0; attempt < 3; ++attempt) {
        const IPAddress targets[2] = {IPAddress(255, 255, 255, 255), WiFi.broadcastIP()};
        for (int t = 0; t < 2; ++t) {
            if (t == 1 && targets[1] == targets[0]) break;
            udp.beginPacket(targets[t], kDiscoveryPort);
            udp.write(reinterpret_cast<const uint8_t*>(kLine), sizeof(kLine) - 1);
            udp.endPacket();
        }
        for (int waited = 0; waited < 400; waited += 20) {
            if (cancelled(myGen)) {
                udp.stop();
                return false;
            }
            const int n = udp.parsePacket();
            if (n > 0 && n < 400) {
                char buf[400];
                const int got = udp.read(reinterpret_cast<uint8_t*>(buf), sizeof(buf) - 1);
                if (got > 0) {
                    buf[got] = 0;
                    if (nod::pair::parseDiscovery(buf, found)) {
                        ip = udp.remoteIP().toString().c_str();
                        udp.stop();
                        return true;
                    }
                }
            } else if (n > 0) {
                udp.flush();
            }
            delay(20);
        }
    }
    udp.stop();
    return false;
}

// One HTTP call with short timeouts. Returns the status code (<= 0 on transport failure).
int pcHttp(const char* method, const std::string& url, const std::string& body, std::string& out) {
    WiFiClient client;
    HTTPClient http;
    http.setConnectTimeout(1500);
    http.setTimeout(2500);
    http.setReuse(false);
    if (!http.begin(client, url.c_str())) return -1;
    int code;
    if (body.empty()) {
        code = http.sendRequest(method);
    } else {
        http.addHeader("Content-Type", "application/json");
        code = http.sendRequest(method, body.c_str());
    }
    if (code > 0) out = http.getString().c_str();
    http.end();
    return code;
}

void endTask() {
    gLink.running = false;
    vTaskDelete(nullptr);
}

void pcTask(void*) {
    using namespace nod::pair;
    const uint32_t myGen = gLink.gen;
    Discovery found;
    std::string ip;
    if (WiFi.status() != WL_CONNECTED || !discover(myGen, found, ip)) {
        if (!cancelled(myGen)) setPhase(Phase::NoComputer);
        endTask();
        return;
    }
    gLink.ip = ip;
    gLink.port = found.port;
    gLink.name = found.name;
    setPhase(Phase::Requesting);

    uint8_t mac[6] = {};
    WiFi.macAddress(mac);
    const std::string base = "http://" + ip + ":" + std::to_string(found.port);
    std::string reply;
    const int rc = pcHttp("POST", base + "/pair/device/request",
                          requestBody(deviceIdFromMac(mac), "Piblo"), reply);
    std::string requestId;
    {
        JsonDocument doc;
        if (rc == 200 && !deserializeJson(doc, reply)) {
            requestId = doc["request_id"] | "";
            const std::string code = formatCode(doc["code"] | "");
            if (!requestId.empty() && !code.empty()) {
                gLink.code = code;
                setPhase(Phase::Waiting);
            }
        }
    }
    if (gLink.phase != Phase::Waiting) {
        if (!cancelled(myGen)) setPhase(Phase::TimedOut);
        endTask();
        return;
    }

    const uint32_t start = millis();
    while (!cancelled(myGen) && !terminal(gLink.phase)) {
        for (uint32_t w = 0; w < kPollEveryMs && !cancelled(myGen); w += 100) delay(100);
        if (cancelled(myGen)) break;
        std::string body;
        const int code = pcHttp("GET", base + "/pair/device/request/" + requestId, "", body);
        std::string status;
        JsonDocument d;
        if (code == 200 && !deserializeJson(d, body)) {
            status = d["status"] | "";
            if (status == "approved") {
                const std::string token = d["token"] | "";
                const long port = d["port"] | static_cast<long>(found.port);
                if (token.empty() || port < 1 || port > 65535) {
                    setPhase(Phase::TimedOut);
                    break;
                }
                gLink.token = token;
                gLink.port = static_cast<uint16_t>(port);
            }
        }
        Phase next = nextPhase(gLink.phase, code, status);
        next = afterElapsed(next, millis() - start);
        if (next != gLink.phase) setPhase(next);
    }
    endTask();
}

class PairingApp final : public App {
public:
    explicit PairingApp(bool computer = false) : _pc(computer) {}

    const char* id() const override { return _pc ? "connect_pc" : "pairing"; }
    const char* title() const override { return _pc ? "CONNECT TO COMPUTER" : "PAIRING + PHONE"; }
    const char* hints() const override {
        if (!_pc) return "ENTER pair device";
        if (gLink.phase == nod::pair::Phase::Approved) return "ENTER done";
        return canRetry() ? "ENTER try again   ESC back" : "ESC cancel";
    }

    void onEnter() override {
        if (_pc) startPc();
    }
    void onExit() override {
        if (_pc) ++gLink.gen;  // cancel the task; it only touches statics
    }

    void update() override {
        if (!_pc) return;
        if (_startPending && !gLink.running) {
            _startPending = false;
            resetLink();
            _saved = false;
            _shown = nod::pair::Phase::Discovering;
            gLink.running = true;
            if (xTaskCreate(pcTask, "pibloPair", 8192, nullptr, 1, nullptr) != pdPASS) {
                gLink.running = false;
                gLink.phase = nod::pair::Phase::NoComputer;
            }
            invalidate();
        }
        __sync_synchronize();
        const nod::pair::Phase p = gLink.phase;
        if (p == nod::pair::Phase::Approved && !_saved && !_startPending) {
            // Same fields MAZCOREPAIR writes (src/net/control.cpp).
            Cfg.hostAddr = gLink.ip;
            Cfg.hostPort = gLink.port;
            Cfg.hostToken = gLink.token;
            Cfg.firstRunComplete = true;
            Cfg.save();
            Sys.hostAddr = Cfg.hostAddr;
            Sys.hostPort = Cfg.hostPort;
            gLink.token.clear();
            _saved = true;
        }
        if (p != _shown) {
            _shown = p;
            invalidate();
        }
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_pc) {
            if (e.code == KEY_ENTER && canRetry()) {
                startPc();
                return true;
            }
            return false;  // ESC falls through to the shell: back, which cancels the task
        }
        if (e.code == KEY_ENTER) {
            _pair = host::startPairing();
            invalidate();
            return true;
        }
        return false;
    }

    std::string contextSnapshot() const override {
        if (_pc) return "Piblo connecting to a computer (code and Allow)";
        return std::string("Piblo pairing token ID ") + tokenId() +
               "; short-lived phone pairing is hub /pair";
    }

    void render(M5Canvas& g) override {
        if (_pc) {
            renderPc(g);
            return;
        }
        g.fillScreen(BG);
        ui::header(g, "PAIRING + PHONE", host::linkName());
        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, BG);
        g.drawString("TOKEN ID", PAD, BELOW_RULE);
        g.setTextColor(ACCENT, BG);
        g.drawString(tokenId().c_str(), 80, BELOW_RULE);

        g.setTextColor(ACCENT2, BG);
        g.drawString("PAIR DEVICE", PAD, BODY_Y + 35);
        if (_pair.ok) {
            g.setFont(&fonts::Font2);
            g.setTextColor(TEXT, BG);
            g.drawCentreString(_pair.code.c_str(), 120, BODY_Y + 48);
            g.setFont(&fonts::Font0);
            g.setTextColor(DIM, BG);
            g.drawString("ONE USE  -  EXPIRES IN 5 MIN", PAD, BODY_Y + 70);
        } else {
            g.setTextColor(_pair.error.empty() ? TEXT : WARN, BG);
            g.drawString(ui::ellipsis(_pair.error.empty() ? "Press ENTER for a temporary code" : _pair.error, 38).c_str(), PAD, BODY_Y + 52);
        }

        std::string url;
        if (!Cfg.hostRemoteUrl.empty()) {
            url = Cfg.hostRemoteUrl;
            while (!url.empty() && url.back() == '/') url.pop_back();
            url += "/pair/";
        } else if (!Cfg.hostAddr.empty()) {
            url = "http://" + Cfg.hostAddr + ":" + std::to_string(Cfg.hostPort) + "/pair/";
        } else {
            url = "Configure hub first";
        }
        g.setTextColor(DIM, BG);
        g.drawString("OPEN ON PHONE", PAD, BODY_Y + 87);
        g.setTextColor(ACCENT2, BG);
        g.drawString(ui::ellipsis(url, 37).c_str(), PAD, BODY_Y + 101);
    }

private:
    bool canRetry() const {
        return nod::pair::terminal(gLink.phase) && gLink.phase != nod::pair::Phase::Approved;
    }

    void startPc() {
        ++gLink.gen;  // cancels any task still running; update() starts the next one when it ends
        _startPending = true;
        invalidate();
    }

    void line(M5Canvas& g, const char* text, int y, uint16_t color) {
        g.setTextColor(color, BG);
        g.drawCentreString(text, SCREEN_W / 2, y);
    }

    void renderPc(M5Canvas& g) {
        using nod::pair::Phase;
        g.fillScreen(BG);
        ui::header(g, "CONNECT TO COMPUTER", nullptr);
        g.setFont(&fonts::Font2);
        const Phase p = _startPending ? Phase::Discovering : gLink.phase;
        const int y = BODY_Y + 40;
        switch (p) {
            case Phase::Discovering:
            case Phase::Requesting:
                line(g, "Looking for your computer...", y + 10, TEXT);
                break;
            case Phase::Waiting: {
                g.setFont(&fonts::Font4);
                line(g, (std::string("Code ") + gLink.code).c_str(), y - 8, ACCENT);
                g.setFont(&fonts::Font2);
                line(g, "Click Allow on your computer", y + 32, TEXT);
                if (!gLink.name.empty())
                    line(g, ui::ellipsis(std::string("for ") + gLink.name, 30).c_str(), y + 50, DIM);
                break;
            }
            case Phase::Approved:
                line(g, ui::ellipsis(std::string("Connected to ") +
                                         (gLink.name.empty() ? "your computer" : gLink.name), 30).c_str(),
                     y + 10, OK);
                break;
            case Phase::NoComputer:
                line(g, "No computer found.", y, WARN);
                line(g, "Is the Piblo app running", y + 22, TEXT);
                line(g, "on the same Wi-Fi?", y + 40, TEXT);
                break;
            case Phase::Denied:
                line(g, "Not allowed", y + 10, ERR);
                break;
            case Phase::TimedOut:
                line(g, "Timed out,", y + 4, WARN);
                line(g, "press ENTER to try again", y + 24, TEXT);
                break;
        }
    }

    host::PairCode _pair;
    const bool _pc;
    bool _startPending = false;
    bool _saved = false;
    nod::pair::Phase _shown = nod::pair::Phase::Discovering;
};

}  // namespace

App* makePairing() { return new PairingApp(false); }
App* makeConnectPc() { return new PairingApp(true); }

}  // namespace apps
}  // namespace maz
