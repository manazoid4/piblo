#include "../audio/live.h"
#include "mazhost.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include "remote_tls.h"
#include <fw_version.h>  // firmware/nod/include (pure version guard)
#include <WiFi.h>

#include "../core/settings.h"
#include "../core/sys.h"

namespace maz {
namespace host {
namespace {

// Follows the link chosen by host::health(): LAN is always tried first (short
// timeout) there, and the remote HTTPS URL is used only while LAN is down.
bool coreBegin(NodHttp& http, const std::string& path, bool allowRemote = true, bool fast = false) {
    if (Cfg.hostToken.empty() || WiFi.status() != WL_CONNECTED) return false;
    bool remote = false;
    std::string base;
    if (allowRemote && onRemoteLink() && Cfg.hostRemoteUrl.rfind("https://", 0) == 0) {
        base = Cfg.hostRemoteUrl;
        while (!base.empty() && base.back() == '/') base.pop_back();
        remote = true;
    } else if (!Cfg.hostAddr.empty()) {
        base = "http://" + Cfg.hostAddr + ":" + std::to_string(Cfg.hostPort);
    } else {
        return false;
    }
    // fast = UI-thread read: worst case ~4 s instead of ~18 s (ESC is dead while it blocks).
    http.setConnectTimeout(remote ? (fast ? 2500 : 6500) : 1800);
    http.setTimeout(fast ? 2000 : 12000);
    if (!http.open((base + path).c_str(), remote)) return false;
    http.addHeader("Authorization", ("Bearer " + Cfg.hostToken).c_str());
    return true;
}

std::string detailFrom(JsonDocument& doc, const char* fallback) {
    if (doc["detail"].is<const char*>()) return doc["detail"].as<const char*>();
    if (doc["error"].is<const char*>()) return doc["error"].as<const char*>();
    return fallback;
}

CoreJob decodeJob(const String& body, int status) {
    CoreJob out;
    JsonDocument doc;
    if (status < 200 || status >= 300 || deserializeJson(doc, body)) {
        out.error = "invalid hub job response";
        if (!doc.isNull()) out.error = detailFrom(doc, out.error.c_str());
        return out;
    }
    out.id = doc["id"] | "";
    out.state = doc["state"] | "";
    out.action = doc["action"] | "";
    out.project = doc["project"] | "";
    out.output = doc["output"] | "";
    out.error = doc["error"] | "";
    if (doc["ok"].is<bool>()) out.ok = doc["ok"].as<bool>();
    else out.ok = out.state == "queued" || out.state == "running";
    return out;
}

}  // namespace

static CoreInfo gCoreInfo;
const CoreInfo& coreInfo() { return gCoreInfo; }
void setCoreInfo(const CoreInfo& info) { gCoreInfo = info; }
bool updateReady() {
    return Sys.wifiConnected && Sys.hostOnline && gCoreInfo.ok && gCoreInfo.fwUpdateAvailable;
}

CoreInfo fetchCoreInfo() {
    CoreInfo out;
    NodHttp http;
    const bool viaRemote = onRemoteLink();
    if (!coreBegin(http, "/health?fw_version=" NOD_FW_VERSION, true, true)) return out;
    const int status = http.GET();
    const String body = status > 0 ? http.getString() : String();
    http.end();
    JsonDocument doc;
    if (status != 200 || deserializeJson(doc, body)) return out;
    out.ok = true;
    out.version = doc["version"] | "";
    out.fwVersion = doc["fw_latest"]["version"] | "";
    out.fwSha = doc["fw_latest"]["sha"] | "";
    out.fwUpdateAvailable = doc["fw_update_available"].is<bool>() && doc["fw_update_available"].as<bool>();
    // Core tells us its Tailscale Funnel URL. Accept only when read over the LAN
    // (not via the remote link), https, a *.ts.net host, bounded length.
    if (!viaRemote) {
        std::string ru = doc["remote_url"] | "";
        while (!ru.empty() && ru.back() == '/') ru.pop_back();
        static const char kHttps[] = "https://";
        static const char kSuffix[] = ".ts.net";
        const size_t hostLen = ru.size() > 8 ? ru.size() - 8 : 0;
        bool ok = ru.size() <= 96 && ru.rfind(kHttps, 0) == 0 && hostLen > 7 &&
                  ru.compare(ru.size() - 7, 7, kSuffix) == 0;
        for (size_t i = 8; ok && i < ru.size(); i++) {
            const char c = ru[i];
            ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '.';
        }
        if (ok && ru != Cfg.hostRemoteUrl) {
            Cfg.hostRemoteUrl = ru;
            Cfg.save();
        }
        if (ok) out.remoteUrl = ru;
    }
    return out;
}

BuddyPoll buddyPoll() {
    BuddyPoll out;
    NodHttp http;
    if (!coreBegin(http, "/buddy/summary", true, true)) return out;
    const int status = http.GET();
    const String body = status > 0 ? http.getString() : String();
    http.end();
    JsonDocument doc;
    if (status != 200 || deserializeJson(doc, body)) return out;
    out.ok = true;
    out.agent = doc["agent"] | "idle";
    out.count = doc["count"] | 0;
    for (JsonObject it : doc["items"].as<JsonArray>()) {
        BuddyItem b;
        b.id = it["id"] | "";
        b.tool = it["tool"] | "?";
        b.summary = it["summary"] | "";
        b.project = it["project"] | "";
        b.session = it["session_id"] | "";
        b.left = it["left"] | 0;
        b.nod = it["nod"] | false;
        if (!b.id.empty()) out.items.push_back(std::move(b));
    }
    return out;
}

bool buddyDecide(const std::string& id, const std::string& decision, std::string& applied) {
    applied.clear();
    // Generic alert answers ("al-" ids) share this worker job: POST /buddy/alert/{id}/{ack|snooze}.
    const bool alert = id.rfind("al-", 0) == 0;
    if (alert && id.find('/') != std::string::npos) return false;  // id goes into the URL path
    NodHttp http;
    if (!coreBegin(http, alert ? "/buddy/alert/" + id + "/" + decision : "/buddy/decide")) return false;
    http.addHeader("Content-Type", "application/json");
    JsonDocument req;
    req["id"] = id;
    req["decision"] = decision;
    String payload;
    serializeJson(req, payload);
    const int status = http.POST(payload);
    const String body = status > 0 ? http.getString() : String();
    http.end();
    if (status == 404) { applied = "gone"; return true; }  // expired or cancelled at the terminal
    if (alert) { applied = decision; return status == 200; }
    JsonDocument doc;
    if (status != 200 || deserializeJson(doc, body)) return false;
    applied = doc["decision"] | "";
    return true;
}

CoreStatus coreStatus() {
    CoreStatus out;
    NodHttp http;
    if (!coreBegin(http, "/core/status", true, true)) {
        out.error = "hub not paired / Wi-Fi offline";
        return out;
    }
    const int status = http.GET();
    const String body = status > 0 ? http.getString() : String();
    http.end();
    if (status <= 0) {
        Sys.hostOnline = false;
        out.error = "hub unreachable";
        return out;
    }
    JsonDocument doc;
    if (status != 200 || deserializeJson(doc, body)) {
        out.error = "invalid hub response";
        return out;
    }
    Sys.hostOnline = true;
    out.ok = true;
    out.hostname = doc["hostname"] | "hub";
    out.model = doc["ollama_model"] | "";
    out.projects = doc["project_count"] | 0;
    out.ollama = doc["ollama"]["online"] | false;
    return out;
}

std::vector<CoreProject> coreProjects(std::string& error) {
    std::vector<CoreProject> out;
    error.clear();
    NodHttp http;
    if (!coreBegin(http, "/core/projects", true, true)) {
        error = "hub not paired / Wi-Fi offline";
        return out;
    }
    const int status = http.GET();
    const String body = status > 0 ? http.getString() : String();
    http.end();
    if (status <= 0) {
        Sys.hostOnline = false;
        error = "hub unreachable";
        return out;
    }
    JsonDocument doc;
    if (status != 200 || deserializeJson(doc, body)) {
        error = "invalid hub response";
        return out;
    }
    Sys.hostOnline = true;
    for (JsonObject item : doc["projects"].as<JsonArray>()) {
        CoreProject p;
        p.name = item["name"] | "project";
        p.branch = item["branch"] | "";
        p.kind = item["kind"] | "";
        p.dirty = item["dirty"] | 0;
        out.push_back(p);
        if (out.size() >= 32) break;
    }
    return out;
}

Reply coreAction(const std::string& action, const std::string& project) {
    Reply out;
    NodHttp http;
    if (!coreBegin(http, "/core/action")) {
        out.error = "hub not paired / Wi-Fi offline";
        return out;
    }
    http.addHeader("Content-Type", "application/json");
    JsonDocument request;
    request["action"] = action;
    request["project"] = project;
    String payload;
    serializeJson(request, payload);
    const int status = http.POST(payload);
    const String body = status > 0 ? http.getString() : String();
    http.end();
    out.status = status;
    if (status <= 0) {
        Sys.hostOnline = false;
        out.error = "hub unreachable";
        return out;
    }
    JsonDocument doc;
    if (deserializeJson(doc, body)) {
        out.error = "invalid hub response";
        return out;
    }
    if (status < 200 || status >= 300) {
        out.error = detailFrom(doc, "hub action failed");
        return out;
    }
    Sys.hostOnline = true;
    out.ok = doc["ok"] | false;
    out.text = doc["output"] | "";
    if (out.text.empty()) out.text = out.ok ? "Action complete" : "Action failed";
    if (!out.ok) out.error = out.text;
    return out;
}

CoreJob coreStartJob(const std::string& action, const std::string& project) {
    CoreJob out;
    NodHttp http;
    if (!coreBegin(http, "/core/job")) {
        out.error = "hub not paired / Wi-Fi offline";
        return out;
    }
    http.addHeader("Content-Type", "application/json");
    JsonDocument request;
    request["action"] = action;
    request["project"] = project;
    String payload;
    serializeJson(request, payload);
    const int status = http.POST(payload);
    const String body = status > 0 ? http.getString() : String();
    http.end();
    if (status <= 0) {
        Sys.hostOnline = false;
        out.error = "hub unreachable";
        return out;
    }
    Sys.hostOnline = status >= 200 && status < 300;
    return decodeJob(body, status);
}

CoreJob coreJob(const std::string& id) {
    CoreJob out;
    if (id.empty()) { out.error = "job id missing"; return out; }
    NodHttp http;
    if (!coreBegin(http, "/core/job/" + id)) {
        out.error = "hub not paired / Wi-Fi offline";
        return out;
    }
    const int status = http.GET();
    const String body = status > 0 ? http.getString() : String();
    http.end();
    if (status <= 0) {
        Sys.hostOnline = false;
        out.error = "hub unreachable";
        return out;
    }
    Sys.hostOnline = status >= 200 && status < 300;
    return decodeJob(body, status);
}

}  // namespace host
}  // namespace maz

// ---- Wi-Fi firmware update (U2) ---------------------------------------------------------------
#include <M5Unified.h>
#include <Preferences.h>
#include <esp_ota_ops.h>
#include <mbedtls/sha256.h>

namespace maz {
namespace host {
namespace {

void fwProgress(const char* line, int pct) {
    auto& d = M5.Display;
    d.fillRect(0, 50, 240, 40, 0);
    d.setTextColor(0xFFFF, 0);
    d.setTextDatum(top_left);
    d.drawString(line, 8, 52);
    d.drawRect(8, 72, 224, 10, 0xFFFF);
    d.fillRect(8, 72, 224 * pct / 100, 10, 0x07E0);
}

// Last failure: kept in RAM and NVS so /api/status and Core can explain it without a cable.
struct FwDiag {
    String stage, err;
    int code = 0;
    bool loaded = false;
} gDiag;

void diagLoad() {
    if (gDiag.loaded) return;
    gDiag.loaded = true;
    Preferences p;
    p.begin("nodfw", false);
    gDiag.stage = p.getString("lstage", "");
    gDiag.err = p.getString("lerr", "");
    gDiag.code = p.getInt("lcode", 0);
    p.end();
}

void diagSet(const char* stage, const char* err, int code) {
    gDiag.loaded = true;
    gDiag.stage = stage;
    gDiag.err = err;
    gDiag.code = code;
    Preferences p;
    p.begin("nodfw", false);
    p.putString("lstage", stage);
    p.putString("lerr", err);
    p.putInt("lcode", code);
    p.end();
}

const char* otaStateName(esp_ota_img_states_t s) {
    switch (s) {
        case ESP_OTA_IMG_NEW: return "new";
        case ESP_OTA_IMG_PENDING_VERIFY: return "pending_verify";
        case ESP_OTA_IMG_VALID: return "valid";
        case ESP_OTA_IMG_INVALID: return "invalid";
        case ESP_OTA_IMG_ABORTED: return "aborted";
        default: return "undefined";
    }
}

String runningOtaState(const esp_partition_t* running, esp_ota_img_states_t* raw = nullptr) {
    esp_ota_img_states_t st = ESP_OTA_IMG_UNDEFINED;
    const esp_err_t e = esp_ota_get_state_partition(running, &st);
    if (raw) *raw = e == ESP_OK ? st : ESP_OTA_IMG_UNDEFINED;
    return e == ESP_OK ? String(otaStateName(st)) : String("n/a ") + esp_err_to_name(e);
}

bool otadataPresent() {
    return esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_OTA, nullptr) != nullptr;
}

// Best effort: tell Core what happened, so the PC log has it even when nobody is looking at the device.
void fwReport(const char* stage, const char* err, int code, size_t size) {
    const esp_partition_t* run = esp_ota_get_running_partition();
    const esp_partition_t* nxt = esp_ota_get_next_update_partition(nullptr);
    JsonDocument d;
    d["stage"] = stage;
    d["error"] = err;
    d["code"] = code;
    d["slot"] = run ? run->label : "";
    d["next_slot"] = nxt ? nxt->label : "";
    d["size"] = size;
    d["build"] = NOD_FW_SHA;
    d["battery"] = Sys.batteryPct;
    d["ota_state"] = runningOtaState(run);
    d["otadata"] = otadataPresent();
    String body;
    serializeJson(d, body);
    NodHttp h;
    if (!coreBegin(h, "/fw/report", false)) return;
    h.setTimeout(4000);
    h.addHeader("Content-Type", "application/json");
    h.POST(body);
    h.end();
}

// why = plain words for the screen; code = the ESP error (ESP_OK when it is not an ESP failure).
bool fwFail(const char* stage, const char* why, esp_err_t code = ESP_OK, size_t size = 0) {
    const char* name = code == ESP_OK ? "" : esp_err_to_name(code);
    Serial.printf("[fw] FAIL %s: %s (%s 0x%x)\n", stage, why, name, (unsigned)code);
    diagSet(stage, code == ESP_OK ? String(why).c_str() : (String(why) + " / " + name).c_str(), (int)code);
    auto& d = M5.Display;
    d.fillRect(0, 50, 240, 60, 0);
    d.setTextColor(0xFFFF, 0);
    d.setTextDatum(top_left);
    d.drawString(why, 8, 52);
    d.drawString(name, 8, 68);
    d.drawString(stage, 8, 84);
    fwReport(stage, why, (int)code, size);
    delay(4000);
    return false;
}

}  // namespace

// Fragment for /api/status: ,"fw_...": fields (starts with a comma).
String fwStatusJson() {
    diagLoad();
    const esp_partition_t* run = esp_ota_get_running_partition();
    const esp_partition_t* nxt = esp_ota_get_next_update_partition(nullptr);
    String j = ",\"fw_build\":\"" NOD_FW_SHA "\"";
    j += ",\"fw_running_slot\":\"" + String(run ? run->label : "") + "\"";
    j += ",\"fw_next_slot\":\"" + String(nxt ? nxt->label : "") + "\"";
    j += ",\"fw_slot_size\":" + String(nxt ? (unsigned)nxt->size : 0u);
    j += ",\"fw_ota_state\":\"" + runningOtaState(run) + "\"";
    j += ",\"fw_otadata_present\":"; j += otadataPresent() ? "true" : "false";
    j += ",\"fw_last_stage\":\"" + gDiag.stage + "\"";
    String e = gDiag.err;
    e.replace("\\", "/");
    e.replace("\"", "'");
    j += ",\"fw_last_error\":\"" + e + "\"";
    j += ",\"fw_last_error_code\":" + String(gDiag.code);
    return j;
}

bool fwUpdate() {
    live::end();  // free the live buffers and socket: the https download needs the heap
    WiFi.setSleep(false);  // steady throughput while the flash is busy erasing/writing
    NodHttp m;
    if (!coreBegin(m, "/fw/manifest", false)) return fwFail("manifest", "Update needs home Wi-Fi");
    const int ms = m.GET();
    const String mb = ms > 0 ? m.getString() : String();
    m.end();
    JsonDocument doc;
    if (ms != 200 || deserializeJson(doc, mb)) return fwFail("manifest", "no firmware on hub");
    const size_t size = doc["size"] | 0;
    const String want = doc["sha256"] | "";
    const esp_partition_t* running = esp_ota_get_running_partition();
    const esp_partition_t* target = esp_ota_get_next_update_partition(nullptr);
    if (!target) return fwFail("precheck", "No free OTA slot", ESP_ERR_NOT_FOUND, size);
    if (target == running) return fwFail("precheck", "No free slot: reinstall via Launcher", ESP_ERR_OTA_PARTITION_CONFLICT, size);
    if (size < 65536 || size > target->size) return fwFail("precheck", "Image too big for slot", ESP_ERR_INVALID_SIZE, size);
    if (want.length() != 64) return fwFail("manifest", "bad manifest");
    // Never downgrade: a stale or older staged image is refused here, not only by Core.
    if (!nod::fwIsNewer(doc["version"] | "", NOD_FW_VERSION)) return fwFail("precheck", "Already up to date", ESP_ERR_INVALID_VERSION, size);

    // A fresh Launcher install can leave this image PENDING_VERIFY, which makes esp_ota_begin refuse.
    esp_ota_img_states_t st;
    runningOtaState(running, &st);
    if (st == ESP_OTA_IMG_PENDING_VERIFY) esp_ota_mark_app_valid_cancel_rollback();

    // Erase the spare slot BEFORE opening the download, so the HTTP stream is not left idle during the erase.
    esp_ota_handle_t ota = 0;
    const esp_err_t be = esp_ota_begin(target, size, &ota);
    if (be != ESP_OK) {
        return fwFail("begin", be == ESP_ERR_OTA_PARTITION_CONFLICT ? "No free slot: reinstall via Launcher"
                      : be == ESP_ERR_OTA_ROLLBACK_INVALID_STATE ? "Boot not confirmed yet"
                      : "Cannot prepare spare slot", be, size);
    }

    NodHttp http;
    if (!coreBegin(http, "/fw/latest.bin", false)) { esp_ota_abort(ota); return fwFail("download", "Update needs home Wi-Fi"); }
    http.setTimeout(20000);
    const int code = http.GET();
    if (code != 200) { esp_ota_abort(ota); http.end(); return fwFail("download", "download refused", (esp_err_t)code, size); }

    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    mbedtls_sha256_starts(&sha, 0);
    static uint8_t buf[2048];
    WiFiClient* stream = http.getStreamPtr();
    size_t done = 0;
    uint32_t last = millis(), shown = 0;
    while (done < size) {
        const size_t n = stream->readBytes(buf, min(sizeof(buf), size - done));
        if (n == 0) {
            if (millis() - last > 15000) { esp_ota_abort(ota); http.end(); return fwFail("download", "download stalled", ESP_ERR_TIMEOUT, done); }
            delay(2);
            continue;
        }
        last = millis();
        const esp_err_t we = esp_ota_write(ota, buf, n);
        if (we != ESP_OK) { esp_ota_abort(ota); http.end(); return fwFail("write", "flash write failed", we, done); }
        mbedtls_sha256_update(&sha, buf, n);
        done += n;
        if (millis() - shown > 400) { shown = millis(); fwProgress("Updating Piblo...", static_cast<int>(done * 100 / size)); }
    }
    http.end();
    uint8_t out[32];
    mbedtls_sha256_finish(&sha, out);
    mbedtls_sha256_free(&sha);
    char hex[65];
    for (int i = 0; i < 32; ++i) snprintf(hex + i * 2, 3, "%02x", out[i]);
    if (!want.equalsIgnoreCase(hex)) { esp_ota_abort(ota); return fwFail("verify", "checksum mismatch", ESP_ERR_INVALID_CRC, size); }
    const esp_err_t ee = esp_ota_end(ota);
    if (ee != ESP_OK) return fwFail("verify", "image invalid", ee, size);

    Preferences p;
    p.begin("nodfw", false);
    p.putString("prev", running->label);
    p.putInt("boots", 0);
    p.end();
    const esp_err_t se = esp_ota_set_boot_partition(target);
    if (se != ESP_OK) {
        Preferences q;  // the guard marker is only meaningful once the boot slot really changed
        q.begin("nodfw", false);
        q.remove("prev");
        q.end();
        return fwFail("boot", se == ESP_ERR_NOT_FOUND ? "Boot table missing" : "cannot set boot slot", se, size);
    }
    diagSet("", "", 0);
    fwReport("ok", "rebooting into new build", 0, size);
    fwProgress("Updated - rebooting", 100);
    Serial.printf("[fw] OK %u bytes -> %s\n", (unsigned)size, target->label);
    delay(600);
    ESP.restart();
    return true;
}

void fwBootGuard() {
    Preferences p;
    p.begin("nodfw", false);
    if (p.isKey("prev")) {
        const int boots = p.getInt("boots", 0) + 1;
        p.putInt("boots", boots);
        if (boots > 3) {  // fresh update restarted 3 times without ever running stable: go back
            const String prev = p.getString("prev", "");
            const esp_partition_t* back = esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, prev.c_str());
            p.remove("prev");
            p.end();
            if (back && esp_ota_set_boot_partition(back) == ESP_OK) { Serial.println("[fw] rollback"); ESP.restart(); }
            return;
        }
    }
    p.end();
}

void fwMarkGood() {
    Preferences p;
    p.begin("nodfw", false);
    if (p.isKey("prev")) {
        // Launcher lists every slot that holds a valid image. Now this build has run
        // stable, blank the header of the slot we updated FROM so Launcher shows one Piblo.
        // Only that slot (saved by fwUpdate), never another app's partition.
        const esp_partition_t* run = esp_ota_get_running_partition();
        const esp_partition_t* old = esp_partition_find_first(
            ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, p.getString("prev", "").c_str());
        if (run && old && old != run && old->subtype >= ESP_PARTITION_SUBTYPE_APP_OTA_MIN &&
            old->subtype < ESP_PARTITION_SUBTYPE_APP_OTA_MAX) {
            esp_ota_img_states_t st;
            // A pending image would roll back to the old slot on reset: confirm it first.
            if (esp_ota_get_state_partition(run, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY)
                esp_ota_mark_app_valid_cancel_rollback();
            const esp_err_t e = esp_partition_erase_range(old, 0, 4096);
            Serial.printf("[fw] old slot %s header erased: %s\n", old->label, esp_err_to_name(e));
        }
        p.remove("prev"); p.remove("boots"); Serial.println("[fw] marked good");
    }
    p.end();
}

}  // namespace host
}  // namespace maz
