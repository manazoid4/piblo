#include "field.h"

#include <Arduino.h>
#include <WiFi.h>

#include <algorithm>
#include <ctime>
#include <poll_backoff.h>

#include "../apps/apps.h"
#include "../audio/sfx.h"
#include "../net/host_worker.h"
#include "../net/mazhost.h"
#include "../storage/store.h"
#include "alerts.h"
#include "launcher.h"
#include "notify.h"
#include "phone_input.h"
#include "quiet.h"
#include "settings.h"
#include "shell.h"
#include "sys.h"

namespace maz {
namespace field {
namespace {

struct QuickOption { const char* id; const char* label; };
constexpr QuickOption QUICK[] = {
    {"talk", "TALK"}, {"braindump", "NOTE"}, {"laptop", "LAP"}, {"beam", "BEAM"},
    {"shift", "SHIFT"}, {"focus25", "FOCUS"}, {"lock", "LOCK"},
    {"play_pause", "PLAY"}, {"mute", "MUTE"}, {"recall", "RECALL"},
    {"flow", "FLOW"}, {"launcher", "LAUNCH"},
};
constexpr int QUICK_COUNT = sizeof(QUICK) / sizeof(QUICK[0]);
constexpr time_t VALID_WALL_CLOCK = 1704067200;

std::string gContext;
uint32_t gShiftStartedMs = 0;
uint32_t gLastCountsAt = 0;
uint32_t gNextOutboxTry = 0;
uint32_t gOutboxBackoffMs = 15000;
uint32_t gNextBeamPoll = 0;
uint32_t gNextCoreInfo = 0;
bool gSystemRequested = false;
bool gWorkRequested = false;
bool gTodayRequested = false;
host::TodayStatus gToday;
bool gReminderDue = false;

std::string& quickSetting(int slot) {
    if (slot == 1) return Cfg.quick1;
    if (slot == 2) return Cfg.quick2;
    if (slot == 3) return Cfg.quick3;
    return Cfg.quick4;
}

int quickIndex(const std::string& id) {
    for (int i = 0; i < QUICK_COUNT; ++i)
        if (id == QUICK[i].id) return i;
    return -1;
}

bool dueNow(const store::Record& r) {
    if (r.status != "open" || !r.due) return false;
    if (r.dueIsUptime) return Sys.uptimeSeconds() >= r.due;
    const time_t now = time(nullptr);
    return now >= VALID_WALL_CLOCK && static_cast<uint32_t>(now) >= r.due;
}

void refreshCounts() {
    if (!store::ready()) {
        Sys.outboxQueued = 0;
        Sys.beamUnread = 0;
        gReminderDue = false;
        return;
    }
    uint8_t outbox = 0;
    for (const auto& r : store::loadRecords("outbox", 64))
        if (r.status == "queued" && outbox < 255) ++outbox;
    Sys.outboxQueued = outbox;

    uint8_t beams = 0;
    for (const auto& r : store::loadRecords("beam", 64))
        if (r.status == "open" && beams < 255) ++beams;
    Sys.beamUnread = beams;

    gReminderDue = false;
    for (const auto& r : store::loadRecords("reminder", 32)) {
        if (dueNow(r)) { gReminderDue = true; break; }
    }
}

store::Record* findRecord(std::vector<store::Record>& rows, const std::string& id) {
    for (auto& row : rows) if (row.id == id) return &row;
    return nullptr;
}

void saveReminderFrom(const host::Reply& reply) {
    if (reply.reminderTitle.empty() || !reply.reminderDelay) return;
    store::Record reminder;
    reminder.kind = "reminder";
    reminder.status = "open";
    reminder.title = reply.reminderTitle;
    reminder.source = "voice-outbox";
    apps::scheduleReminder(reminder, reply.reminderDelay);
    store::addRecord(reminder);
}

void finishOutboxAudio(const host_worker::OutboxAudioResult& result) {
    auto rows = store::loadRecords(nullptr, 512);
    store::Record* queued = findRecord(rows, result.recordId);
    if (!queued) return;
    if (!result.reply.ok) {
        gNextOutboxTry = millis() + gOutboxBackoffMs;
        gOutboxBackoffMs = std::min<uint32_t>(gOutboxBackoffMs * 2u, 300000u);
        return;
    }

    queued->status = "done";
    store::updateRecord(*queued);
    if (!result.wavPath.empty()) store::remove(result.wavPath);

    store::Record answer;
    answer.kind = "inbox";
    answer.status = "open";
    answer.title = result.context.empty() ? "COMM / delayed" : "CONTEXT ASK / delayed";
    answer.body = result.reply.text;
    answer.source = result.reply.provider.empty() ? "hub" : result.reply.provider;
    answer.ref = result.recordId;
    store::addRecord(answer);
    saveReminderFrom(result.reply);

    gOutboxBackoffMs = 15000;
    gNextOutboxTry = millis() + 500;
    notify::post(Note::Success, "Outbox sent", "answer saved in Recall");
    refreshCounts();
}

void finishOutboxBeam(const host_worker::OutboxBeamResult& result) {
    auto rows = store::loadRecords(nullptr, 512);
    store::Record* queued = findRecord(rows, result.recordId);
    if (!queued) return;
    if (!result.reply.ok) {
        // Core rejected it (400/422, e.g. blank text): retrying forever blocks the queue.
        if (result.reply.status == 400 || result.reply.status == 422) {
            queued->status = "failed";
            store::updateRecord(*queued);
            gNextOutboxTry = millis() + 500;
            notify::post(Note::Error, "Beam rejected", result.reply.error.c_str());
            refreshCounts();
            return;
        }
        gNextOutboxTry = millis() + gOutboxBackoffMs;
        gOutboxBackoffMs = std::min<uint32_t>(gOutboxBackoffMs * 2u, 300000u);
        return;
    }
    queued->status = "done";
    store::updateRecord(*queued);
    gOutboxBackoffMs = 15000;
    gNextOutboxTry = millis() + 500;
    notify::post(Note::Success, "Beam delivered", "copied/saved on hub");
    refreshCounts();
}

void finishBeamPull(const host::BeamMessage& msg) {
    const uint32_t cadence = Cfg.fieldMode ? 45000u : 15000u;
    gNextBeamPoll = millis() + cadence;
    for (const auto& e : msg.events) {
        std::string detail = e.source.empty() ? e.text : e.source + ": " + e.text;
        if (detail.size() > 160) detail.resize(160);
        if (e.kind == "alert") { alerts::onEvent(e); continue; }  // generic alerts: Buddy overlay + priority table
        if (e.kind == "text_input") { phone::onTextInput(e); continue; }  // phone keyboard: literal insert only
        // Quiet mode decides the sound only; the toast is always posted.
        using quiet::Ev;
        const Ev ev = e.kind == "done" ? Ev::Done : e.kind == "usage" ? Ev::Usage
                    : e.kind == "needs" ? Ev::Needs : e.kind == "error" ? Ev::Error : Ev::None;
        if (ev == Ev::None) continue;
        const bool chirp = quiet::shouldChirp(quiet::level(), ev);
        if (ev == Ev::Done) {
            if (chirp) sfx::agentDone();  // plays the chirp, mutes the generic toast beep
            else sfx::skipNextNote();
            notify::post(Note::Success, "Agent done", detail);
        } else {
            if (!chirp) sfx::skipNextNote();
            if (ev == Ev::Usage) notify::post(Note::Warn, "Usage", detail);
            else if (ev == Ev::Needs) notify::post(Note::Warn, "Needs you", detail);
            else notify::post(Note::Error, "Agent error", detail);
        }
    }
    if (msg.halted >= 0) {
        const bool value = msg.halted == 1;
        if (!Sys.haltKnown || Sys.halted != value) shell::invalidate();
        Sys.halted = value;
        Sys.haltKnown = true;
    } else if (msg.haltCheckFailed && Sys.haltKnown) {
        Sys.haltKnown = false;
        shell::invalidate();
    }
    if (!msg.ok || !msg.hasMessage || msg.text.empty()) return;

    store::Record r;
    r.kind = "beam";
    r.status = "open";
    r.title = msg.kind == "link" ? "BEAM / LINK" : "BEAM / TEXT";
    r.body = msg.text.substr(0, 2000);
    r.source = "laptop";
    r.ref = msg.id;
    if (store::addRecord(r)) {
        notify::post(Note::Success, "Beam received", r.title);
        refreshCounts();
    }
}

void finishSystem(const host::SystemStatus& s) {
    gSystemRequested = false;
    Sys.laptopStatusOk = s.ok;
    Sys.laptopStatusAt = millis();
    if (!s.ok) return;
    Sys.laptopCpuPct = s.cpuPct;
    Sys.laptopRamPct = s.ramPct;
    Sys.laptopBatteryPct = s.batteryPct;
    Sys.laptopCharging = s.charging;
    Sys.laptopGpuAvailable = s.gpuAvailable;
    Sys.laptopGpuPct = s.gpuPct;
    Sys.laptopVramUsedMb = s.vramUsedMb;
    Sys.laptopVramTotalMb = s.vramTotalMb;
    Sys.laptopGpuTempC = s.gpuTempC;
    Sys.ollamaOnline = s.ollamaOnline;
    Sys.ollamaLoaded = s.ollamaLoaded;
    Sys.ollamaModel = s.ollamaModel;
    Sys.ollamaVramMb = s.ollamaVramMb;
    Sys.ollamaContext = s.ollamaContext;
    shell::invalidate();
}

void finishToday(const host::TodayStatus& value) {
    gTodayRequested = false;
    gToday = value;
    shell::invalidate();
}

void finishWork(const host::WorkSummary& s) {
    gWorkRequested = false;
    if (!s.ok) {  // keep last-known values on a failed poll — never blank/zero
        if (Sys.workError != s.error) {
            Sys.workError = s.error;
            Serial.println(("[work] poll failed: " + s.error).c_str());
            if (!Sys.workLoaded) notify::post(Note::Warn, "WORK sync failed", s.error);
            shell::invalidate();
        }
        return;
    }
    Sys.workError.clear();
    Sys.workLoaded = true;
    Sys.workReceivedAt = millis();
    Sys.workTrackCount = static_cast<uint8_t>(std::min(s.trackCount, SysState::WORK_MAX_TRACKS));
    for (int i = 0; i < Sys.workTrackCount; ++i) {
        Sys.workTrackId[i] = s.tracks[i].id;
        Sys.workTrackLabel[i] = s.tracks[i].shortLabel;
        Sys.workTrackPrimaryEventTypeId[i] = s.tracks[i].primaryEventTypeId;
        Sys.workTrackToday[i] = s.tracks[i].todayTotal;
        Sys.workTrackHasTarget[i] = s.tracks[i].hasTarget;
        Sys.workTrackTarget[i] = s.tracks[i].target;
    }
    for (int d = 0; d < SysState::WORK_HISTORY_DAYS; ++d) Sys.workSeven[d] = s.sevenDay[d];
    Sys.workNextAction = s.nextAction;
    shell::invalidate();
}

void finishWorkIncrement(const host_worker::WorkIncrementResult& result) {
    if (!result.reply.ok) {
        notify::post(Note::Error, "WORK not saved", result.reply.error.c_str());
        return;
    }
    for (int i = 0; i < Sys.workTrackCount; ++i) {
        if (Sys.workTrackId[i] != result.trackId) continue;
        Sys.workTrackToday[i] += 1;
        notify::post(Note::Success, "+1 saved", Sys.workTrackLabel[i].c_str());
        break;
    }
    gWorkRequested = true;
    shell::invalidate();
}

bool submitNextOutbox() {
    if (!store::ready()) return false;
    const auto rows = store::loadRecords("outbox", 64);
    // Oldest first: failed work preserves the order it was captured.
    for (auto it = rows.rbegin(); it != rows.rend(); ++it) {
        const auto& r = *it;
        if (r.status != "queued") continue;
        if (r.source == "beam")
            return host_worker::submitOutboxBeam(r.id, r.body);
        if (r.source == "talk" || r.source == "talk-context") {
            if (r.ref.empty() || !store::fs() || !store::fs()->exists(r.ref.c_str())) {
                store::Record broken = r;
                broken.status = "failed";
                broken.body = "recording missing";
                store::updateRecord(broken);
                refreshCounts();
                continue;
            }
            const std::string context = r.source == "talk-context" ? r.body : "";
            return host_worker::submitOutboxAudio(r.id, r.ref, context);
        }
    }
    return false;
}

std::string elapsed(uint32_t seconds) {
    char out[16];
    const uint32_t h = seconds / 3600;
    const uint32_t m = (seconds / 60) % 60;
    const uint32_t s = seconds % 60;
    snprintf(out, sizeof(out), "%02lu:%02lu:%02lu",
             static_cast<unsigned long>(h), static_cast<unsigned long>(m),
             static_cast<unsigned long>(s));
    return out;
}

}  // namespace

void begin() {
    Sys.fieldMode = Cfg.fieldMode;
    refreshCounts();
    gNextBeamPoll = millis() + 5000;
}

void update() {
    host_worker::reapStale();
    static bool good = false;
    if (!good && millis() > 45000) { good = true; host::fwMarkGood(); }
    if (Sys.shiftRunning) Sys.shiftSeconds = (millis() - gShiftStartedMs) / 1000;

    if (host_worker::state() == host_worker::State::Done) {
        switch (host_worker::jobKind()) {
            case host_worker::JobKind::OutboxAudio: {
                host_worker::OutboxAudioResult r;
                if (host_worker::takeOutboxAudioResult(r)) finishOutboxAudio(r);
                break;
            }
            case host_worker::JobKind::OutboxBeam: {
                host_worker::OutboxBeamResult r;
                if (host_worker::takeOutboxBeamResult(r)) finishOutboxBeam(r);
                break;
            }
            case host_worker::JobKind::BeamPull: {
                host::BeamMessage r;
                if (host_worker::takeBeamPullResult(r)) finishBeamPull(r);
                break;
            }
            case host_worker::JobKind::CoreInfo: {
                host::CoreInfo r;
                if (host_worker::takeCoreInfoResult(r)) host::setCoreInfo(r);
                break;
            }
            case host_worker::JobKind::SystemStatus: {
                host::SystemStatus r;
                if (host_worker::takeSystemStatusResult(r)) finishSystem(r);
                break;
            }
            case host_worker::JobKind::Today: {
                host::TodayStatus result;
                if (host_worker::takeTodayResult(result)) finishToday(result);
                break;
            }
            case host_worker::JobKind::WorkSummary: {
                host::WorkSummary r;
                if (host_worker::takeWorkSummaryResult(r)) finishWork(r);
                break;
            }
            case host_worker::JobKind::WorkIncrement: {
                host_worker::WorkIncrementResult r;
                if (host_worker::takeWorkIncrementResult(r)) finishWorkIncrement(r);
                break;
            }
            default: break;  // COMM/PC results belong to their caller.
        }
    }

    const uint32_t countCadence = Cfg.fieldMode ? 12000u : 5000u;
    if (millis() - gLastCountsAt >= countCadence) {
        gLastCountsAt = millis();
        refreshCounts();
    }

    if (host_worker::state() != host_worker::State::Idle ||
        !host::configured() || WiFi.status() != WL_CONNECTED)
        return;

    if (gSystemRequested) {
        if (host_worker::submitSystemStatus()) return;
    }

    if (gWorkRequested) {
        if (host_worker::submitWorkSummary()) return;
    }
    if (gTodayRequested) {
        if (host_worker::submitToday()) return;
    }

    if (Sys.outboxQueued && static_cast<int32_t>(millis() - gNextOutboxTry) >= 0) {
        if (submitNextOutbox()) return;
    }

    if (static_cast<int32_t>(millis() - gNextCoreInfo) >= 0) {
        gNextCoreInfo = millis() + 30000u;
        if (host_worker::submitCoreInfo()) return;
    }

    if (static_cast<int32_t>(millis() - gNextBeamPoll) >= 0) {
        // Schedule before submitting: returning first re-polled every tick and
        // kept the single worker busy, so Call reported "busy".
        // Hub offline (the previous poll failed): back off to 30 s instead of burning a connect timeout every 15 s.
        static uint8_t offlinePolls = 0;
        offlinePolls = Sys.hostOnline ? 0 : (offlinePolls < 255 ? offlinePolls + 1 : 255);
        gNextBeamPoll = millis() + nod::backoffMs(Cfg.fieldMode ? 45000u : 15000u, offlinePolls, 30000u);
        host_worker::submitBeamPull();
    }
}

void armContext(const std::string& appId, const std::string& snapshot) {
    std::string clean = snapshot;
    for (char& c : clean) if (c == '\n' || c == '\r' || c == '\t') c = ' ';
    if (clean.size() > 220) clean.resize(220);
    gContext = appId;
    if (!clean.empty()) gContext += ": " + clean;
}

const std::string& context() { return gContext; }
bool contextArmed() { return !gContext.empty(); }
void clearContext() { gContext.clear(); }

std::string nowText() {
    if (Sys.agentQuestion) return "AGENT NEEDS PIBLO";
    if (Sys.outboxQueued) return "OUTBOX " + std::to_string(Sys.outboxQueued) + " WAITING";
    if (Sys.shiftRunning) return "SHIFT " + elapsed(Sys.shiftSeconds).substr(0, 5);
    if (gReminderDue) return "REMINDER DUE";
    if (Sys.beamUnread) return "BEAM " + std::to_string(Sys.beamUnread) + " NEW";
    if (Sys.agentsStale) return std::to_string(Sys.agentsStale) + " AGENTS STALE";
    if (Sys.agentsWaiting) return std::to_string(Sys.agentsWaiting) + " AGENTS WAIT";
    if (host::configured() && !Sys.hostOnline) return "HUB OFFLINE / QUEUE SAFE";
    if (Cfg.fieldMode) return "FIELD READY";
    return "READY";
}

const char* quickId(int slot) {
    std::string& id = quickSetting(std::max(1, std::min(slot, 4)));
    const int idx = quickIndex(id);
    return idx >= 0 ? QUICK[idx].id : QUICK[slot - 1].id;
}

const char* quickLabel(int slot) {
    const int idx = quickIndex(quickId(slot));
    return idx >= 0 ? QUICK[idx].label : "?";
}

bool runQuick(int slot) {
    const std::string id = quickId(slot);
    if (id == "talk" || id == "braindump" || id == "laptop" || id == "beam" ||
        id == "shift" || id == "recall" || id == "flow")
        return shell::pushById(id.c_str());
    if (id == "focus25") {
        shell::focus::start(25 * 60, "Quick focus");
        notify::post(Note::Success, "Focus started", "25 minutes");
        return true;
    }
    if (id == "launcher") { launcher::reboot(); return true; }
    if (id == "lock" || id == "play_pause" || id == "mute") {
        if (host_worker::submitPcAction(id, false)) {
            notify::post(Note::Info, "hub command queued", quickLabel(slot));
            return true;
        }
        notify::post(Note::Warn, "hub busy", "try again in a moment");
        return false;
    }
    return false;
}

void cycleQuick(int slot) {
    slot = std::max(1, std::min(slot, 4));
    std::string& current = quickSetting(slot);
    int idx = quickIndex(current);
    if (idx < 0) idx = slot - 1;
    current = QUICK[(idx + 1) % QUICK_COUNT].id;
    Cfg.save();
    notify::post(Note::Success, "Quick key changed", quickLabel(slot));
}

void toggleFieldMode() {
    Cfg.fieldMode = !Cfg.fieldMode;
    Sys.fieldMode = Cfg.fieldMode;
    Cfg.save();
    gNextBeamPoll = millis() + (Cfg.fieldMode ? 45000u : 5000u);
    notify::post(Note::Success, Cfg.fieldMode ? "FIELD mode ON" : "FIELD mode OFF",
                 Cfg.fieldMode ? "lower background + faster dim" : "normal cadence restored");
    shell::invalidate();
}

void toggleShift() {
    if (!Sys.shiftRunning) {
        gShiftStartedMs = millis();
        Sys.shiftSeconds = 0;
        Sys.shiftRunning = true;
        notify::post(Note::Success, "Shift started", "NOW will keep elapsed time");
        shell::invalidate();
        return;
    }
    Sys.shiftSeconds = (millis() - gShiftStartedMs) / 1000;
    Sys.shiftRunning = false;
    store::Record r;
    r.kind = "shift";
    r.status = "done";
    r.title = "Shift";
    r.body = "Elapsed " + elapsed(Sys.shiftSeconds);
    r.source = "field";
    store::addRecord(r);
    notify::post(Note::Success, "Shift saved", elapsed(Sys.shiftSeconds).c_str());
    shell::invalidate();
}

void toggleHalt() {
    if (!host::configured()) {
        notify::post(Note::Warn, "Pause asks needs Core", "pair with PC first");
        return;
    }
    // Unknown state chooses only the safe direction (deny future asks).
    const bool target = !Sys.haltKnown || !Sys.halted;
    if (host::haltNow(target)) {
        Sys.halted = target;
        Sys.haltKnown = true;
        notify::post(target ? Note::Warn : Note::Info,
                     target ? "Asks paused" : "Asks resumed",
                     target ? "Running tools are NOT stopped" : "New asks still need approval");
    } else {
        Sys.haltKnown = false;
        notify::post(Note::Error, "Pause not confirmed", "Core unreachable");
    }
    shell::invalidate();
}

std::string shiftElapsedText() { return elapsed(Sys.shiftSeconds); }

void requestSystemStatus() {
    gSystemRequested = true;
    if (!host::configured()) {
        Sys.laptopStatusOk = false;
        Sys.laptopStatusAt = millis();
    }
}

void requestWorkSummary() { gWorkRequested = true; }

void requestToday() {
    gTodayRequested = true;
    if (!host::configured()) {
        gToday = host::TodayStatus{};
        gToday.error = "Core unreachable";
        gTodayRequested = false;
    }
}

const host::TodayStatus& today() { return gToday; }

bool requestWorkIncrement(const std::string& trackId, const std::string& eventTypeId) {
    return host_worker::submitWorkIncrement(trackId, eventTypeId);
}

void queueBeam(const std::string& text) {
    if (text.empty()) return;
    store::Record r;
    r.kind = "outbox";
    r.status = "queued";
    r.title = "BEAM to hub";
    r.body = text.substr(0, 2000);
    r.source = "beam";
    if (store::addRecord(r)) {
        gNextOutboxTry = millis();
        refreshCounts();
        notify::post(Note::Success, "Beam queued", "sends when hub is reachable");
    } else {
        notify::post(Note::Error, "Beam not saved", store::backendName());
    }
}

}  // namespace field
}  // namespace maz
