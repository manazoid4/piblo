#include "mazhost.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include "remote_tls.h"
#include <WiFi.h>

#include <string.h>
#include <utility>
#include <vector>

#include "../core/settings.h"
#include "../core/sys.h"
#include "../storage/store.h"

namespace maz {
namespace host {
namespace {

bool gRemote = false;
uint32_t gFastLinkAt = 0;  // millis() of the last host request that kept WiFi modem sleep off


std::vector<std::pair<std::string, bool>> bases() {
    std::vector<std::pair<std::string, bool>> out;
    if (!Cfg.hostAddr.empty()) {
        out.push_back({"http://" + Cfg.hostAddr + ":" +
                           std::to_string(Cfg.hostPort),
                       false});
    }
    if (!Cfg.hostRemoteUrl.empty()) {
        std::string remote = Cfg.hostRemoteUrl;
        while (!remote.empty() && remote.back() == '/') remote.pop_back();
        if (remote.rfind("https://", 0) == 0) out.push_back({remote, true});
    }
    return out;
}

const char* route() {
    return talkRouteApiName(Cfg.talkRoute);
}

bool beginRequest(NodHttp& http, const std::string& base, const char* path,
                  bool remote) {
    // Modem sleep (the ESP32 default) parks every TCP ACK/reply until the next AP beacon
    // (~100-300 ms each), which throttles uploads and delays Core's reply. Stay awake while
    // talking; linkIdle() restores power saving.
    // Only the voice path: background polls (/nudge, /health) must not hold the radio awake.
    if (!strncmp(path, "/turn", 5) || !strncmp(path, "/speak", 6) || !strncmp(path, "/say", 4) ||
        !strncmp(path, "/session", 8)) {
        WiFi.setSleep(false);
        gFastLinkAt = millis();
    }
    http.setConnectTimeout(remote ? 6500 : 1800);
    http.setTimeout(60000);
    const String url = (base + path).c_str();
    const bool begun = http.open(url, remote);
    if (!begun) return false;
    http.addHeader("Authorization", ("Bearer " + Cfg.hostToken).c_str());
    return true;
}

void markOnline(bool remote) {
    gRemote = remote;
    Sys.hostOnline = true;
}

Reply decodeReply(HTTPClient& http, int status) {
    Reply out;
    out.status = status;
    const String body = http.getString();
    JsonDocument doc;
    const auto error = deserializeJson(doc, body);
    if (status >= 200 && status < 300 && !error) {
        out.ok = true;
        out.text = doc["reply"] | "";
        if (out.text.empty() && doc["result"].is<const char*>())
            out.text = doc["result"].as<const char*>();
        if (out.text.empty()) out.text = doc["summary"] | "";
        out.transcript = doc["text"] | "";
        if (out.transcript.empty()) out.transcript = doc["transcript"] | "";
        out.provider = doc["provider"] | "";
        out.speakId = doc["speak"]["id"] | "";
        out.speakParts = out.speakId.empty() ? 0 : (uint8_t)(doc["speak"]["parts"] | 0);
        JsonArray commands = doc["commands"].as<JsonArray>();
        if (!commands.isNull() && commands.size() &&
            std::string(commands[0]["type"] | "") == "reminder.create") {
            out.reminderTitle = commands[0]["title"] | "";
            out.reminderDelay = commands[0]["delay_seconds"] | 0;
        }
    } else {
        out.error = error ? "invalid host response"
                          : static_cast<const char*>(doc["detail"] | "host request failed");
    }
    http.end();
    return out;
}

Reply upload(const char* path, const std::string& wavPath,
             const std::vector<std::pair<std::string, std::string>>& headers,
             bool idempotent = true) {
    Reply out;
    if (!configured() || WiFi.status() != WL_CONNECTED || !store::ready()) {
        out.error = "host offline";
        return out;
    }
    File file = store::fs()->open(wavPath.c_str(), FILE_READ);
    if (!file) {
        Serial.printf("[upload] cannot open %s\n", wavPath.c_str());
        out.error = "recording missing";
        return out;
    }

    for (const auto& base : bases()) {
        file.seek(0);
        NodHttp http;
        if (!beginRequest(http, base.first, path, base.second)) continue;
        http.addHeader("Content-Type", "audio/wav");
        for (const auto& header : headers)
            http.addHeader(header.first.c_str(), header.second.c_str());
        const int status = http.sendRequest("POST", &file, file.size());
        Serial.printf("[upload] %s%s size=%u status=%d\n", base.first.c_str(), path,
                      static_cast<unsigned>(file.size()), status);
        if (status <= 0) {
            http.end();
            // A paste types on the PC, so only try the next address when nothing was
            // sent (refused / header failed); a lost reply must not type it twice.
            if (!idempotent && status != HTTPC_ERROR_CONNECTION_REFUSED &&
                status != HTTPC_ERROR_SEND_HEADER_FAILED) {
                file.close();
                Sys.hostOnline = false;
                out.error = "no reply - check the PC";
                return out;
            }
            continue;
        }
        file.close();
        markOnline(base.second);
        return decodeReply(http, status);
    }
    file.close();
    Sys.hostOnline = false;
    out.error = "hub unreachable";
    return out;
}

Reply jsonPost(const char* path, const std::string& json) {
    Reply out;
    if (!configured() || WiFi.status() != WL_CONNECTED) {
        out.error = "host offline";
        return out;
    }
    for (const auto& base : bases()) {
        NodHttp http;
        if (!beginRequest(http, base.first, path, base.second)) continue;
        http.addHeader("Content-Type", "application/json");
        const int status = http.POST(json.c_str());
        if (status <= 0) {
            http.end();
            continue;
        }
        markOnline(base.second);
        return decodeReply(http, status);
    }
    Sys.hostOnline = false;
    out.error = "hub unreachable";
    return out;
}

}  // namespace

bool onRemoteLink() { return Sys.hostOnline && gRemote; }

void linkIdle(bool force) {
    if (gFastLinkAt && (force || millis() - gFastLinkAt > 20000)) {
        WiFi.setSleep(true);
        gFastLinkAt = 0;
    }
}

bool configured() {
    return !Cfg.hostToken.empty() && (!Cfg.hostAddr.empty() || !Cfg.hostRemoteUrl.empty());
}

const char* linkName() {
    if (!Sys.hostOnline) return "OFFLINE";
    return gRemote ? "REMOTE" : "LAN";
}

bool health() {
    if (!configured() || WiFi.status() != WL_CONNECTED) {
        Sys.hostOnline = false;
        return false;
    }
    for (const auto& base : bases()) {
        NodHttp http;
        if (!beginRequest(http, base.first, "/health", base.second)) continue;
        const int status = http.GET();
        http.end();
        if (status <= 0) continue;
        if (status == 200) {
            markOnline(base.second);
            return true;
        }
        Sys.hostOnline = false;
        return false;
    }
    Sys.hostOnline = false;
    return false;
}

std::string startSession() {
    if (!configured() || WiFi.status() != WL_CONNECTED) return "";
    for (const auto& base : bases()) {
        NodHttp http;
        if (!beginRequest(http, base.first, "/session/start", base.second)) continue;
        http.addHeader("X-MAZ-Route", route());
        const int status = http.POST("");
        if (status <= 0) {
            http.end();
            continue;
        }
        const String body = http.getString();
        http.end();
        JsonDocument doc;
        if (status != 200 || deserializeJson(doc, body)) return "";
        markOnline(base.second);
        return doc["session_id"] | "";
    }
    Sys.hostOnline = false;
    return "";
}

PairCode startPairing() {
    PairCode out;
    if (!configured() || WiFi.status() != WL_CONNECTED) {
        out.error = "hub offline";
        return out;
    }
    for (const auto& base : bases()) {
        NodHttp http;
        if (!beginRequest(http, base.first, "/pair/start", base.second)) continue;
        const int status = http.POST("");
        if (status <= 0) {
            http.end();
            continue;
        }
        const String body = http.getString();
        http.end();
        JsonDocument doc;
        if (deserializeJson(doc, body)) {
            out.error = "invalid pairing response";
            return out;
        }
        if (status < 200 || status >= 300) {
            out.error = doc["detail"] | "pairing unavailable";
            return out;
        }
        const std::string code = doc["code"] | "";
        if (code.size() != 8) {
            out.error = "invalid pairing code";
            return out;
        }
        markOnline(base.second);
        out.ok = true;
        out.code = code;
        out.expiresInSeconds = doc["expires_in_seconds"] | 300;
        return out;
    }
    Sys.hostOnline = false;
    out.error = "hub unreachable";
    return out;
}

Reply talkText(const std::string& session, const std::string& text) {
    JsonDocument doc;
    doc["session_id"] = session;
    doc["route"] = route();
    doc["text"] = text;
    String body;
    serializeJson(doc, body);
    return jsonPost("/turn/text", body.c_str());
}

Reply talkAudio(const std::string& session, const std::string& wavPath) {
    std::vector<std::pair<std::string, std::string>> headers = {
        {"X-MAZ-Session", session}, {"X-MAZ-Route", route()}};
    if (Cfg.ttsEnabled) headers.push_back({"X-MAZ-Speak", "1"});  // Core starts TTS as soon as the LLM answers
    return upload("/turn/raw", wavPath, headers);
}

Reply transcribe(const std::string& wavPath) {
    return upload("/transcribe/raw", wavPath, {{"X-MAZ-Route", route()}});
}

Reply flowPaste(const std::string& wavPath) {
    // Piblo Flow: Core cleans the text and types it into the focused PC window.
    return upload("/dictate?target=pc-paste", wavPath, {}, /*idempotent=*/false);
}

Reply brainDump(const std::string& wavPath,
                const std::vector<uint32_t>& highlights) {
    String marks = "[";
    for (size_t i = 0; i < highlights.size(); ++i) {
        if (i) marks += ',';
        marks += highlights[i];
    }
    marks += ']';
    return upload("/braindump/raw", wavPath,
                  {{"X-MAZ-Highlights", marks.c_str()}});
}

Reply pcAction(const std::string& action) {
    JsonDocument doc;
    doc["action"] = action;
    String body;
    serializeJson(doc, body);
    return jsonPost("/pc/action", body.c_str());
}

namespace {
// Core route that answers with a WAV, saved to wavPath. body == nullptr means GET.
bool postWav(const char* path, const std::string& body, const std::string& wavPath, bool get = false) {
    if (!configured() || WiFi.status() != WL_CONNECTED || !store::ready()) return false;

    for (const auto& base : bases()) {
        NodHttp http;
        if (!beginRequest(http, base.first, path, base.second)) continue;
        http.addHeader("X-MAZ-Route", route());
        int status;
        if (get) {
            status = http.GET();
        } else {
            http.addHeader("Content-Type", "application/json");
            status = http.POST(body.c_str());
        }
        if (status <= 0) {
            http.end();
            continue;
        }
        if (status != 200) {
            Serial.printf("[speak] %s -> HTTP %d\n", path, status);
            http.end();
            return false;
        }
        store::remove(wavPath);
        File out = store::fs()->open(wavPath.c_str(), FILE_WRITE);
        if (!out) {
            Serial.printf("[speak] SD open failed: %s\n", wavPath.c_str());
            http.end();
            return false;
        }
        const int expect = http.getSize();
        const int written = http.writeToStream(&out);
        Serial.printf("[speak] path=%s status=%d bytes=%d\n", wavPath.c_str(), status, written);
        out.close();
        http.end();
        if (written > 44 && (expect <= 0 || written == expect)) {
            markOnline(base.second);
            return true;
        }
        Serial.printf("[speak] bad download: wrote %d expected %d\n", written, expect);
        store::remove(wavPath);
        return false;
    }
    return false;
}
}  // namespace

bool speak(const std::string& text, const std::string& wavPath) {
    if (text.empty()) return false;
    JsonDocument doc;
    doc["text"] = text.size() > 1400 ? text.substr(0, 1400) : text;
    String body;
    serializeJson(doc, body);
    return postWav("/speak", body.c_str(), wavPath);
}

bool say(const std::string& text, const std::string& wavPath) {
    if (text.empty()) return false;
    JsonDocument doc;
    doc["text"] = text.size() > 300 ? text.substr(0, 300) : text;
    String body;
    serializeJson(doc, body);
    return postWav("/say", body.c_str(), wavPath);
}

bool speakPart(const std::string& id, uint8_t part, const std::string& wavPath) {
    if (id.empty() || id.size() > 32) return false;
    for (char ch : id)
        if (!isalnum((unsigned char)ch)) return false;
    const std::string path = "/speak?id=" + id + "&part=" + std::to_string(part);
    return postWav(path.c_str(), "", wavPath, true);
}

bool voiceList(std::vector<VoiceItem>& out, std::string& error) {
    out.clear();
    if (!configured() || WiFi.status() != WL_CONNECTED) {
        error = "host offline";
        return false;
    }
    for (const auto& base : bases()) {
        NodHttp http;
        if (!beginRequest(http, base.first, "/voices", base.second)) continue;
        const int status = http.GET();
        if (status <= 0) {
            http.end();
            continue;
        }
        const String body = http.getString();
        http.end();
        JsonDocument doc;
        if (status != 200 || deserializeJson(doc, body)) {
            error = "voices unavailable";
            return false;
        }
        markOnline(base.second);
        for (JsonObject v : doc["voices"].as<JsonArray>()) {
            VoiceItem item;
            item.name = v["name"] | "";
            item.id = v["reference_id"] | "";
            item.description = v["description"] | "";
            item.current = v["current"] | false;
            if (!item.id.empty()) out.push_back(item);
        }
        return !out.empty();
    }
    error = "hub unreachable";
    return false;
}

bool voiceSelect(const std::string& id, const std::string& name) {
    JsonDocument doc;
    doc["reference_id"] = id;
    doc["name"] = name;
    String body;
    serializeJson(doc, body);
    return jsonPost("/voices/select", body.c_str()).ok;
}

bool voicePreview(const std::string& id, const std::string& wavPath) {
    JsonDocument doc;
    doc["reference_id"] = id;
    String body;
    serializeJson(doc, body);
    return postWav("/voices/preview", body.c_str(), wavPath);
}

Assurance assurance() {
    Assurance out;
    if (!configured() || WiFi.status() != WL_CONNECTED) {
        out.error = "host offline";
        return out;
    }
    for (const auto& base : bases()) {
        NodHttp http;
        if (!beginRequest(http, base.first, "/nudge", base.second)) continue;
        const int status = http.GET();
        if (status <= 0) {
            http.end();
            continue;
        }
        const String body = http.getString();
        http.end();
        JsonDocument doc;
        if (status != 200 || deserializeJson(doc, body)) {
            out.error = "nudge unavailable";
            return out;
        }
        markOnline(base.second);
        out.ok = true;
        out.state = doc["state"] | "ALL_SYNCED";
        out.working = doc["counts"]["working"] | 0;
        out.waiting = doc["counts"]["waiting"] | 0;
        out.needsNudge = doc["counts"]["needsNudge"] | 0;
        out.overdue = doc["counts"]["overdue"] | 0;
        out.attention = doc["counts"]["attention"] | 0;
        out.questionForMaz = doc["counts"]["questionForMaz"] | 0;
        for (JsonObject item : doc["agents"].as<JsonArray>()) {
            Agent a;
            a.id = item["sessionId"] | "";
            a.provider = item["provider"] | "agent";
            a.name = item["projectName"] | "";
            if (a.name.empty()) a.name = item["projectId"] | a.provider.c_str();
            a.state = item["state"] | "ALL_SYNCED";
            a.sessionState = item["sessionState"] | "offline";
            JsonArray refs = item["evidenceRefs"].as<JsonArray>();
            if (!refs.isNull() && refs.size()) a.evidence = refs[0].as<const char*>();
            out.agents.push_back(a);
        }
        Sys.agentsWorking = out.working;
        Sys.agentsWaiting = out.waiting;
        Sys.agentsStale = out.needsNudge + out.overdue;
        Sys.agentQuestion = out.questionForMaz > 0;
        Sys.nudgeDue = out.needsNudge + out.overdue > 0;
        return out;
    }
    Sys.hostOnline = false;
    out.error = "hub unreachable";
    return out;
}

Reply sendNudge(const std::string& sessionId) {
    return jsonPost(("/nudge/" + sessionId + "/nudge").c_str(), "{}");
}

}  // namespace host
}  // namespace maz
