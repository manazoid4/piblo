#pragma once

#include <stdint.h>
#include <string>
#include <vector>

namespace maz {
namespace host {

struct Reply {
    bool        ok = false;
    int         status = 0;
    std::string text;
    std::string transcript;
    std::string provider;
    std::string error;
    std::string reminderTitle;
    uint32_t    reminderDelay = 0;
    std::string speakId;       // Core planned the voice: GET /speak?id=&part=N
    uint8_t     speakParts = 0;
};

struct PairCode {
    bool ok = false;
    std::string code;
    int expiresInSeconds = 0;
    std::string error;
};

struct Agent {
    std::string id;
    std::string name;
    std::string provider;
    std::string state;
    std::string sessionState;
    std::string evidence;
};

struct Assurance {
    bool ok = false;
    std::string state = "OFFLINE";
    int working = 0;
    int waiting = 0;
    int needsNudge = 0;
    int overdue = 0;
    int attention = 0;
    int questionForMaz = 0;
    std::vector<Agent> agents;
    std::string error;
};

struct CoreProject {
    std::string name;
    std::string branch;
    std::string kind;
    int dirty = 0;
};

struct CoreStatus {
    bool ok = false;
    std::string hostname;
    std::string model;
    int projects = 0;
    bool ollama = false;
    std::string error;
};

struct CoreJob {
    bool ok = false;
    std::string id;
    std::string state;
    std::string action;
    std::string project;
    std::string output;
    std::string error;
};

// Notify-only agent ping from Core /buddy/events (kind: done|usage|needs|error).
// Data only: nothing in this struct can approve, deny or run anything.
struct AgentEvent {
    std::string kind;
    std::string text;
    std::string source;  // free-text agent name, may be empty
    // kind == "alert" (generic notification, see docs/ALERTS.md); otherwise unused.
    std::string alertId, alertKind, title, body, priority;
    uint8_t     alertActions = 0;  // alert_policy::ACT_* bits offered by Core
    // kind == "text_input" (phone keyboard, see docs/protocol/PHONE-KEYBOARD.md).
    std::string textInputId, textInputMode;
    int64_t     eventTs = 0;  // Core wall-clock seconds for the 2-minute TTL
};

struct BeamMessage {
    std::vector<AgentEvent> events;  // new /buddy/events since the last poll
    int8_t halted = -1;              // Core state: -1 unknown, 0 off, 1 on
    bool haltCheckFailed = false;
    bool ok = false;
    bool hasMessage = false;
    std::string id;
    std::string kind;
    std::string text;
    std::string error;
};

// "What did my agents do today" counts from Core GET /buddy/today.
constexpr int TODAY_MAX_AGENTS = 9;  // 8 named + "other"
struct TodayAgent { std::string source; int done = 0; };
struct TodayStatus {
    bool ok = false;
    int done = 0, needs = 0, errors = 0, usage = 0, approved = 0, denied = 0;
    int agentCount = 0;
    TodayAgent agents[TODAY_MAX_AGENTS];
    bool hasLast = false;
    std::string lastKind, lastText, lastSource;
    std::string error;
};

struct SystemStatus {
    bool ok = false;
    int cpuPct = -1;
    int ramPct = -1;
    int batteryPct = -1;
    bool charging = false;
    bool gpuAvailable = false;
    int gpuPct = -1;
    int vramUsedMb = 0;
    int vramTotalMb = 0;
    int gpuTempC = -1;
    bool ollamaOnline = false;
    bool ollamaLoaded = false;
    std::string ollamaModel;
    int ollamaVramMb = 0;
    int ollamaContext = 0;
    std::string error;
};

// WORK Consistency glance payload. Server-side hard cap (spec): at most 4
// pinned tracks, a fixed 7-length day array — mirrored here as fixed-size,
// bounded fields so a malformed/oversized response cannot overflow anything.
constexpr int WORK_MAX_TRACKS = 4;
constexpr int WORK_HISTORY_DAYS = 7;

struct WorkTrack {
    std::string id;
    std::string shortLabel;
    std::string primaryEventTypeId;
    float todayTotal = 0;
    bool hasTarget = false;
    float target = 0;
};

struct WorkSummary {
    bool ok = false;
    int trackCount = 0;
    WorkTrack tracks[WORK_MAX_TRACKS];
    float sevenDay[WORK_HISTORY_DAYS] = {};
    std::string nextAction;
    std::string error;
};

struct TeachDisplay {
    std::string id;
    std::string name;
    int width = 0;
    int height = 0;
    bool primary = false;
};

struct TeachStatus {
    bool ok = false;
    std::string sessionId;
    std::string state;
    std::string transcript;
    std::string error;
    int frameCount = 0;
    int elapsedSeconds = 0;
};

bool configured();
bool health();
void linkIdle(bool force = false);  // re-enable WiFi modem sleep (20 s after the last request, or now if force)
bool onRemoteLink();   // true while the active Core link is the remote HTTPS URL
const char* linkName();
std::string startSession();
PairCode startPairing();
Reply talkText(const std::string& session, const std::string& text);
Reply talkTextContext(const std::string& session, const std::string& text,
                      const std::string& context);
Reply talkAudio(const std::string& session, const std::string& wavPath);
Reply transcribe(const std::string& wavPath);
Reply flowPaste(const std::string& wavPath);  // /dictate?target=pc-paste
Reply brainDump(const std::string& wavPath, const std::vector<uint32_t>& highlights);
Reply pcAction(const std::string& action);
bool speak(const std::string& text, const std::string& wavPath);
// Say tool: Core POST /say (free Fish model only), WAV saved to wavPath.
bool say(const std::string& text, const std::string& wavPath);

// One part of a Core-planned reply (part 0 = first sentence). Blocks until Core has it.
bool speakPart(const std::string& id, uint8_t part, const std::string& wavPath);

// Reply voice (Core /voices). voiceSelect persists the choice on the PC;
// voicePreview downloads a cached "Hi, I'm nod." wav in that voice.
struct VoiceItem {
    std::string name;
    std::string id;
    std::string description;
    bool        current = false;
};
bool voiceList(std::vector<VoiceItem>& out, std::string& error);
bool voiceSelect(const std::string& id, const std::string& name);
bool voicePreview(const std::string& id, const std::string& wavPath);
Assurance assurance();
Reply sendNudge(const std::string& sessionId);

// v0.8 agent workbench. These are compact request/response calls; the Pocket
// displays the useful summary while MAZ Core keeps richer workflow data.
Reply workPlan(const std::string& task, const std::string& project = "");
Reply workCrew(const std::string& task, const std::string& project = "");
Reply workRetro(const std::string& project = "", const std::string& note = "");
Reply workPrompt(const std::string& templateId, const std::string& task,
                 const std::string& project = "");

// Explicit PC screen demonstration capture. Recording is performed by MAZ Core
// and is never a hidden background recorder; the Pocket starts, marks and stops
// a named session.
std::vector<TeachDisplay> teachDisplays(std::string& error);
TeachStatus teachStart(const std::string& displayId);
TeachStatus teachMark(const std::string& sessionId, const std::string& note = "");
TeachStatus teachStop(const std::string& sessionId);
TeachStatus teachStatus(const std::string& sessionId);

// v0.7 FIELD endpoints. Received Beam content is data only and is never used as
// a command. Telemetry is a cached one-shot laptop snapshot, not a stream.
Reply beamSend(const std::string& text);
BeamMessage beamPull();
bool haltNow(bool on);    // immediate authenticated POST; never stops running processes
// Phone-keyboard ack: POST /buddy/text/{id}/ack {"status": inserted|no_field|too_long}.
bool textAck(const std::string& id, const std::string& status);
SystemStatus systemStatus();
TodayStatus todayStatus();
WorkSummary workSummary();
Reply workIncrement(const std::string& trackId, const std::string& eventTypeId);

CoreStatus coreStatus();
// Core /health: its version plus the firmware it holds (fw_latest). Polled in the background.
struct CoreInfo { bool ok = false, fwUpdateAvailable = false; std::string version, fwVersion, fwSha, remoteUrl; };
CoreInfo fetchCoreInfo();
// Core /buddy: Claude Code approvals waiting for a thumb (see docs/APPROVALS.md).
// The Nod: a detailed approval has summary = "THE NOD  PAYMENT" / amount / "to recipient" on three lines; nod = hold-to-approve.
struct BuddyItem { std::string id, tool, summary, project, session; int left = 0; bool nod = false; };
struct BuddyPoll { bool ok = false; std::string agent; int count = 0; std::vector<BuddyItem> items; };
BuddyPoll buddyPoll();                                              // GET /buddy/summary (tiny)
bool buddyDecide(const std::string& id, const std::string& decision, std::string& applied);  // allow|deny|allow_all
const CoreInfo& coreInfo();          // last poll result (cached)
void setCoreInfo(const CoreInfo& info);
bool updateReady();
bool fwUpdate();       // blocking: download Core's firmware into the spare slot, verify sha256, reboot
void fwBootGuard();    // call at boot: roll back to the previous slot if a fresh update never ran stable
void fwMarkGood();     // call once running stable: drop the rollback marker                  // Core holds a different build than this one
std::vector<CoreProject> coreProjects(std::string& error);
Reply coreAction(const std::string& action, const std::string& project);
CoreJob coreStartJob(const std::string& action, const std::string& project);
CoreJob coreJob(const std::string& id);

}  // namespace host
}  // namespace maz
