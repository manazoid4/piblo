// CALL MAZ — hold-to-talk voice conversation with spoken reply playback.
#include <algorithm>
#include <array>
#include <string>
#include <vector>

#include "../audio/sfx.h"
#include "../audio/voice.h"
#include "../core/field.h"
#include "../core/notify.h"
#include "../core/settings.h"
#include "../core/shell.h"
#include "../core/sys.h"
#include "../input/keyboard.h"
#include "../net/host_worker.h"
#include "../net/mazhost.h"
#include "../storage/store.h"
#include "../ui/character.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;
using ui::Phase;

namespace {

std::string gCommSession;

// Word-wrap `text` to REPLY_CHARS columns and draw REPLY_LINES lines from `first`.
void drawCommWrapped(M5Canvas& g, const std::string& text, int y, int first = 0) {
    const std::vector<std::string> lines = ui::wrap(text, REPLY_CHARS);
    g.setFont(&fonts::Font0);
    g.setTextColor(TEXT, BG);
    g.setTextDatum(top_left);
    for (int row = 0; row < REPLY_LINES; ++row) {
        const size_t idx = static_cast<size_t>(first + row);
        if (idx >= lines.size()) break;
        g.drawString(lines[idx].c_str(), PAD, y + row * REPLY_LINE_H);
    }
}

struct ControlAction { const char* key; const char* label; const char* hint; };
constexpr ControlAction CONTROLS[] = {
    {"desktop", "DESKTOP", "show / hide desktop"},
    {"play_pause", "PLAY", "play or pause media"},
    {"mute", "MUTE", "toggle hub sound"},
    {"volume_down", "VOL -", "lower volume"},
    {"volume_up", "VOL +", "raise volume"},
    {"lock", "LOCK", "lock Windows"},
};
constexpr int CONTROL_COUNT = sizeof(CONTROLS) / sizeof(CONTROLS[0]);

const char* controlLabel(const std::string& action) {
    for (const auto& item : CONTROLS) if (action == item.key) return item.label;
    return "PC COMMAND";
}

class CommApp : public App {
public:
    const char* id() const override { return "talk"; }
    const char* title() const override { return "CALL PIBLO"; }

    const char* hints() const override {
        if (_controlMode) return _confirm ? "Y send to PC  N cancel" : "</> choose  ENTER send  C back";
        if (voice::state() == voice::State::Listening)
            return field::contextArmed() ? "SPACE release to ask" : "SPACE release to send";
        if (working()) return canCancel() ? "ESC cancel" : "ESC leave, Piblo keeps going";
        if (voice::isPlaying()) return "SPACE stop  P replay";
        if (!_err.empty()) return "SPACE retry  W wifi";
        if (!_reply.empty()) return "SPACE talk  P replay  ENTER say";
        if (linkSentence(true)) return "SPACE record  W wifi";
        return "SPACE hold to talk  ENTER say";
    }

    void onEnter() override {
        _scroll = 0;
        _controlMode = false;
        _confirm = false;
        _discard = false;
        _sending = host_worker::busy();
        consumeWorkerResult();
        if (KB.held(KEY_SPACE) && !host_worker::busy()) beginTake();
        invalidate();
    }

    void onExit() override {
        host::linkIdle(true);
        voice::stopPlayback();
        if (_sink) {
            const bool stopped = voice::state() == voice::State::Listening ? voice::stop() : true;
            const std::string path = _sink->path();
            delete _sink;
            _sink = nullptr;
            if (stopped && !path.empty()) {
                queueRaw(path, "Call recording kept", field::context());
                field::clearContext();
            }
        }
        if (_haveTake && !_takePath.empty()) {
            queueRaw(_takePath, "Call kept", field::context());
            field::clearContext();
            _takePath.clear();
            _haveTake = false;
        }
    }

    bool onKey(const KeyEvent& e) override {
        if (_controlMode) {
            if (!e.down) return false;
            if (e.code == KEY_C) { _controlMode = false; sfx::select(); invalidate(); return true; }
            if (e.code == KEY_LEFT) { _controlSel = (_controlSel + CONTROL_COUNT - 1) % CONTROL_COUNT; sfx::select(); invalidate(); return true; }
            if (e.code == KEY_RIGHT) { _controlSel = (_controlSel + 1) % CONTROL_COUNT; sfx::select(); invalidate(); return true; }
            // Nothing reaches the PC until Maz approves it here: ENTER asks, Y sends.
            if (_confirm) {
                if (e.code == KEY_Y) { _confirm = false; runControl(); return true; }
                if (e.code == KEY_N || e.code == KEY_ESC || e.code == KEY_C) { _confirm = false; sfx::select(); invalidate(); return true; }
                return true;  // swallow other keys while the question is up
            }
            if (e.code == KEY_ENTER) { _confirm = true; sfx::select(); invalidate(); return true; }
            if (e.code == KEY_SPACE) { _controlMode = false; beginTake(); return true; }
            return false;
        }

        if (e.down && e.code == KEY_SPACE) {
            _speechCancelled = true;  // late parts must not restart a reply the user just cut
            if (voice::isPlaying()) voice::stopPlayback();
            consumeWorkerResult();
            if (working()) {
                notify::post(Note::Info, "Piblo is thinking", canCancel() ? "ESC cancels" : "result will wait for you");
                return true;
            }
            if (voice::state() != voice::State::Listening) beginTake();
            return true;
        }
        if (!e.down && e.code == KEY_SPACE) {
            Serial.printf("[call] space up state=%d\n", static_cast<int>(voice::state()));
            if (voice::state() == voice::State::Listening) endTake();
            return true;
        }
        if (!e.down) return false;

        // ESC while MAZ thinks: stop waiting, drop the reply, stay on this screen.
        if (e.code == KEY_ESC && canCancel() && voice::state() != voice::State::Listening) {
            _discard = true;
            _speechCancelled = true;  // parts of the cancelled reply may still land: never play them
            _sending = false;
            voice::stopPlayback();
            sfx::select();
            notify::post(Note::Info, "Cancelled", "SPACE to ask again");
            invalidate();
            return true;
        }
        if (e.code == KEY_ENTER && !working() && voice::state() != voice::State::Listening) {
            shell::pushById("say");
            return true;
        }
        if (e.code == KEY_W && !working() && voice::state() != voice::State::Listening) {
            shell::pushById("network");
            return true;
        }

        if (e.code == KEY_C && !host_worker::busy() && voice::state() != voice::State::Listening) {
            _controlMode = true;
            voice::stopPlayback();
            sfx::select();
            invalidate();
            return true;
        }
        if (e.code == KEY_N && !host_worker::busy()) {
            gCommSession.clear();
            _reply.clear();
            field::clearContext();
            voice::stopPlayback();
            notify::post(Note::Info, "New call", "conversation cleared");
            invalidate();
            return true;
        }
        if (e.code == KEY_A && !host_worker::busy()) {
            Cfg.talkRoute = (Cfg.talkRoute + 1) % TALK_ROUTE_COUNT;
            Cfg.save();
            notify::post(Note::Info, "AI route", routeName());
            invalidate();
            return true;
        }
        if (e.code == KEY_V && !host_worker::busy()) {
            Cfg.ttsEnabled = !Cfg.ttsEnabled;
            Cfg.save();
            if (!Cfg.ttsEnabled) voice::stopPlayback();
            notify::post(Note::Info, "Voice replies", Cfg.ttsEnabled ? "ON - Piblo will speak" : "OFF - text only");
            invalidate();
            return true;
        }
        if (e.code == KEY_P && !_speechPath.empty() && !voice::isPlaying()) { replaySpeech(); return true; }
        if (e.code == KEY_DOWN && !_reply.empty()) { ++_scroll; invalidate(); return true; }
        if (e.code == KEY_UP && _scroll > 0) { --_scroll; invalidate(); return true; }
        return false;
    }

    void update() override {
        if (!host_worker::busy()) host::linkIdle();
        if (_sending && _sendAt && millis() >= _sendAt) startWorker();
        if (host_worker::busy()) queueSpeechParts(host_worker::speechPartsReady(), host_worker::speechPartsExpected());
        consumeWorkerResult();
        if (voice::state() == voice::State::Listening || voice::isPlaying()) {
            invalidate();
        } else if (host_worker::busy() && millis() - _lastWorkerPaint >= 250) {
            _lastWorkerPaint = millis();
            invalidate();
        }
    }

    std::string contextSnapshot() const override {
        if (!_reply.empty()) return "Call Piblo last reply: " + _reply.substr(0, 180);
        return field::contextArmed() ? "Call Piblo preparing screen question" : "Call Piblo voice line";
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        if (_controlMode) { renderControl(g); return; }

        // One state word, one short reason, then the reply text.
        std::string early;
        const bool busy = working() || _sending;
        if (voice::state() == voice::State::Listening) {
            const std::string t = "00:" + two(voice::elapsedSeconds());
            state(g, Phase::Listening,
                  (t + (field::contextArmed() ? "  asking about screen" : "  release to send")).c_str());
            ui::progress(g, PAD, REPLY_Y, SCREEN_W - PAD * 2, 6, voice::level(), LIVE);
        } else if (working() && host_worker::peekTalkText(early)) {
            state(g, Phase::Thinking, "voice loading...");
            drawCommWrapped(g, early, REPLY_Y, 0);
        } else if (busy) {
            const std::string why = host_worker::state() == host_worker::State::Queued ? "waiting for hub" : "Piblo is working on it";
            state(g, Phase::Thinking, (why + (_thinkAt ? "  " + std::to_string((millis() - _thinkAt) / 1000) + "s" : "")).c_str());
        } else if (voice::isPlaying()) {
            state(g, Phase::Speaking, "SPACE stops the voice");
            drawCommWrapped(g, _reply, REPLY_Y, _scroll);
        } else if (!_err.empty()) {
            state(g, Phase::Error, _err.c_str());
            g.setFont(&fonts::Font0);
            g.setTextColor(ERR, BG);
            g.drawString("ESC back", PAD, REPLY_Y);
        } else if (!_reply.empty()) {
            state(g, Phase::Ready, (std::string(routeName()) + (Cfg.ttsEnabled ? " / voice on" : " / text only")).c_str());
            drawCommWrapped(g, _reply, REPLY_Y, _scroll);
        } else if (const char* link = linkSentence(true)) {
            state(g, Phase::Offline, link);
        } else {
            state(g, Phase::Ready, gCommSession.empty() ? "hold SPACE to talk" : "hold SPACE: next turn");
            g.setFont(&fonts::Font0);
            g.setTextDatum(top_center);
            g.setTextColor(DIM, BG);
            g.drawString("ENTER: type a line instead", SCREEN_W / 2, REPLY_Y + 6);
            g.setTextDatum(top_left);
        }
    }

private:
    static std::string two(uint32_t seconds) {
        const uint32_t s = seconds % 60;
        return s < 10 ? "0" + std::to_string(s) : std::to_string(s);
    }
    const char* routeName() const { return talkRouteLabel(Cfg.talkRoute); }

    // Whether a talk job is in flight that the user has not cancelled.
    bool working() const { return host_worker::busy() && !_discard; }
    bool canCancel() const { return working() && host_worker::jobKind() == host_worker::JobKind::TalkAudio; }

    void state(M5Canvas& g, Phase p, const char* reason) {
        const ui::StatusWord sw = ui::statusWord(p);
        const char* word = sw.text;
        constexpr int GLYPH = 20;
        const bool talk = p == Phase::Ready || p == Phase::Listening || p == Phase::Thinking || p == Phase::Speaking;
        const int SIDE = PAD + (talk ? ui::CHAR_W : GLYPH) + 6;  // word stays centred with room for the figure
        if (talk) {
            // Piblo himself: bobs with the mic while listening, bounces while speaking, sways while thinking.
            ui::character(g, PAD, STATE_Y, p == Phase::Listening ? nod::TALK_LISTENING : p == Phase::Thinking ? nod::TALK_THINKING
                          : p == Phase::Speaking ? nod::TALK_SPEAKING : nod::TALK_IDLE,
                          voice::level(), millis());
        } else {
            ui::glyph(g, PAD, STATE_Y + 4, GLYPH, p);
        }
        g.setFont(&fonts::Font4);
        float scale = STATE_SCALE;  // shrink until the word fits the screen
        g.setTextSize(scale);
        while (scale > 1.f && g.textWidth(word) > SCREEN_W - SIDE * 2) { scale -= 0.25f; g.setTextSize(scale); }
        ui::stateWord(g, SCREEN_W / 2, STATE_Y, p, word, true);
        g.setTextSize(1);
        g.setFont(&fonts::Font2);
        g.setTextColor(DIM, BG);
        g.setTextDatum(top_center);
        g.drawString(ui::ellipsis(reason, 32).c_str(), SCREEN_W / 2, REASON_Y);
        g.setTextDatum(top_left);
    }

    void renderControl(M5Canvas& g) {
        ui::header(g, "PC QUICK CONTROL", host::linkName());
        const int prev = (_controlSel + CONTROL_COUNT - 1) % CONTROL_COUNT;
        const int next = (_controlSel + 1) % CONTROL_COUNT;
        g.setTextDatum(top_center);
        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, BG);
        g.drawString(CONTROLS[prev].label, 35, BODY_Y + 31);
        g.drawString(CONTROLS[next].label, 205, BODY_Y + 31);
        ui::panel(g, 66, BODY_Y + 19, 108, 55);
        g.setFont(&fonts::Font2);
        g.setTextColor(ACCENT, PANEL);
        g.drawString(CONTROLS[_controlSel].label, SCREEN_W / 2, BODY_Y + 33);
        g.setFont(&fonts::Font0);
        g.setTextColor(TEXT, PANEL);
        if (_confirm) g.setTextColor(ACCENT, PANEL);
        g.drawString(_confirm ? "SEND? Y / N" : "ENTER / SEND", SCREEN_W / 2, BODY_Y + 54);
        g.setTextColor(DIM, BG);
        g.drawString(CONTROLS[_controlSel].hint, SCREEN_W / 2, BODY_Y + 83);
        g.setTextDatum(top_left);
    }

    void runControl() {
        if (host_worker::busy()) { notify::post(Note::Info, "PC busy", "wait for current action"); return; }
        const auto& control = CONTROLS[_controlSel];
        if (!host_worker::submitPcAction(control.key)) {
            notify::post(Note::Error, "Command not queued", host_worker::stateName());
            return;
        }
        _controlMode = false;
        _sending = true;
        sfx::confirm();
        invalidate();
    }

    void beginTake() {
        if (working()) return;
        _err.clear();
        discardTake();
        _sink = new voice::WavFileSink("outbox");
        if (!_sink || !voice::start(_sink, 60)) {
            notify::post(Note::Error, "Mic failed", voice::lastError());
            delete _sink;
            _sink = nullptr;
            return;
        }
        sfx::recStart();
        invalidate();
    }

    void endTake() {
        Serial.printf("[call] endTake listening=%d sink=%d\n", voice::state() == voice::State::Listening, _sink != nullptr);
        if (!_sink || !voice::stop()) {
            notify::post(Note::Error, "Recording failed", voice::lastError());
            delete _sink;
            _sink = nullptr;
            return;
        }
        sfx::recStop();
        _takePath = _sink->path();
        _haveTake = true;
        delete _sink;
        _sink = nullptr;
        _sending = true;
        _thinkAt = millis();
        _sendAt = millis() + 120;
        invalidate();
    }

    void startWorker() {
        if (_discard && host_worker::busy()) { _sendAt = millis() + 200; return; }  // cancelled job still finishing
        Serial.printf("[call] startWorker have=%d path=%s busy=%d\n", _haveTake, _takePath.c_str(), host_worker::busy());
        _sendAt = 0;
        if (!_haveTake || _takePath.empty()) { _sending = false; return; }
        std::string speechPath;
        removeSpeechFiles();
        _speechCancelled = false;
        _partsQueued = 0;
        _skipLogged = 0;
        if (Cfg.ttsEnabled && store::ready()) speechPath = store::newPath("cache", "wav");
        const std::string context = field::context();
        if (!host_worker::submitTalkAudio(gCommSession, _takePath, speechPath, context)) {
            // A background poll may hold the worker for a moment: wait up to 8 s
            // instead of failing the call outright.
            if (!_waitSince) _waitSince = millis();
            if (millis() - _waitSince < 30000) { _sendAt = millis() + 200; return; }
            _waitSince = 0;
            _sending = false;
            notify::post(Note::Error, "Call busy", host_worker::stateName());
            return;
        }
        _waitSince = 0;
        _discard = false;
        _sendContext = context;
        field::clearContext();
        _takePath.clear();
        _haveTake = false;
        _sending = true;
        invalidate();
    }

    void consumeWorkerResult() {
        if (host_worker::state() != host_worker::State::Done) {
            // A recording still waiting for the worker keeps us sending; clearing
            // it here silently dropped the call whenever a background job ran.
            _sending = working() || _haveTake;
            return;
        }
        if (host_worker::jobKind() == host_worker::JobKind::PcAction) {
            host_worker::PcActionResult result;
            if (!host_worker::takePcActionResult(result)) return;
            _sending = false;
            const char* label = controlLabel(result.action);
            if (result.reply.ok) {
                sfx::confirm();
                notify::post(Note::Success, label, result.reply.text.empty() ? "PC acknowledged" : result.reply.text);
            } else {
                sfx::error();
                notify::post(Note::Error, "Command failed", result.reply.error.empty() ? "PC unavailable" : result.reply.error);
            }
            invalidate();
            return;
        }
        if (host_worker::jobKind() != host_worker::JobKind::TalkAudio) return;
        host_worker::TalkResult result;
        if (!host_worker::takeTalkResult(result)) return;
        _sending = false;
        _sendContext.clear();
        if (_discard) {  // cancelled with ESC: drop the reply and its files
            _discard = false;
            if (!result.wavPath.empty()) store::remove(result.wavPath);
            _speechPath = result.speechPath;
            _speechParts = std::max<uint8_t>(result.speechParts, _partsQueued);
            removeSpeechFiles();
            invalidate();
            return;
        }
        if (!result.session.empty()) gCommSession = result.session;

        Serial.printf("[call] ok=%d status=%d err=%s wav=%s\n", result.reply.ok, result.reply.status,
                      result.reply.error.c_str(), result.wavPath.c_str());
        if (!result.reply.ok) {
            queueRaw(result.wavPath, result.reply.error.empty() ? "PC unavailable" : result.reply.error, result.context);
            _err = failReason(result.reply);
            sfx::error();
            notify::post(Note::Warn, _err, "voice call kept in outbox");
            invalidate();
            return;
        }

        _reply = result.reply.text;
        _err.clear();
        _scroll = 0;
        if (Cfg.ttsEnabled && !result.speechReady && !_reply.empty())
            notify::post(Note::Warn, "Voice failed", "showing text only");
        store::Record answer;
        answer.kind = "inbox";
        answer.status = "open";
        answer.title = result.context.empty() ? "CALL PIBLO" : "ASK SCREEN";
        answer.body = result.reply.text;
        answer.source = result.reply.provider;
        answer.ref = result.wavPath;
        store::addRecord(answer);

        if (!result.reply.reminderTitle.empty() && result.reply.reminderDelay) {
            store::Record reminder;
            reminder.kind = "reminder";
            reminder.status = "open";
            reminder.title = result.reply.reminderTitle;
            reminder.source = "voice";
            scheduleReminder(reminder, result.reply.reminderDelay);
            store::addRecord(reminder);
        }
        if (!result.wavPath.empty()) store::remove(result.wavPath);
        if (result.speechReady && !result.speechPath.empty()) {
            _speechPath = result.speechPath;
            _speechParts = result.speechParts ? result.speechParts : 1;
            queueSpeechParts(_speechParts, _speechParts);  // normally already playing from update()
            voice::holdOpen(false);
        }
        sfx::confirm();
        notify::post(Note::Success, result.context.empty() ? "Piblo answered" : "Screen answer ready",
                     result.reply.provider.empty() ? "hub" : result.reply.provider);
        invalidate();
    }

    // Hand parts to the speaker as they land: part 0 starts playback at once, later parts
    // are queued behind it with no gap. Files stay on SD, so this costs no heap.
    void queueSpeechParts(uint8_t ready, uint8_t expected) {
        if (_partsQueued >= ready) return;
        if (_speechCancelled || !Cfg.ttsEnabled) {
            if (_skipLogged != ready)  // called every frame while the worker is busy: log once per part
                Serial.printf("[speak] skip: cancelled=%d tts=%d ready=%u\n", _speechCancelled, Cfg.ttsEnabled, ready);
            _skipLogged = ready;
            return;
        }
        const std::string base = host_worker::speechBase();
        if (base.empty()) {
            Serial.println("[speak] skip: no speech base path");
            return;
        }
        while (_partsQueued < ready) {
            const std::string path = host_worker::speechPartPath(base, _partsQueued);
            if (_partsQueued == 0) {
                _speechPath = base;
                voice::play(path);
            } else {
                voice::enqueue(path);
            }
            ++_partsQueued;
            if (_partsQueued > _speechParts) _speechParts = _partsQueued;
        }
        if (_partsQueued < expected) voice::holdOpen(true);  // more is coming: do not end on silence
        else voice::holdOpen(false);
    }

    void replaySpeech() {
        voice::play(_speechPath);
        for (uint8_t p = 1; p < _speechParts; ++p) voice::enqueue(host_worker::speechPartPath(_speechPath, p));
    }

    void removeSpeechFiles() {
        if (_speechPath.empty()) return;
        for (uint8_t p = 0; p < _speechParts; ++p) store::remove(host_worker::speechPartPath(_speechPath, p));
        _speechPath.clear();
        _speechParts = 0;
    }

    // On-screen reason for a failed turn: always names what to do (max 30 chars).
    static std::string failReason(const host::Reply& r) {
        const std::string& e = r.error;
        auto has = [&](const char* k) { return e.find(k) != std::string::npos; };
        if (r.status <= 0 || has("unreachable") || has("offline") || has("hub")) {
            const char* link = linkSentence(false);
            return link ? link : "PC not answering. Retry.";
        }
        if (r.status == 429 || r.status == 502 || r.status == 503 || r.status == 504 ||
            has("busy") || has("LLM") || has("brain") || has("model"))
            return "AI busy. SPACE to retry.";
        if (has("transcri") || has("STT") || has("audio") || has("speech") || r.status == 400 || r.status == 415 || r.status == 422)
            return "Not heard. SPACE to retry.";
        return "Call failed. SPACE: retry.";
    }

    void queueRaw(const std::string& path, const std::string& reason,
                  const std::string& context = "") {
        if (path.empty()) return;
        store::Record queued;
        queued.kind = "outbox";
        queued.status = "queued";
        queued.title = context.empty() ? "Call Piblo voice turn" : "Ask Screen voice turn";
        queued.body = context.empty() ? reason : context;
        queued.source = context.empty() ? "talk" : "talk-context";
        queued.ref = path;
        store::addRecord(queued);
    }

    void discardTake() {
        if (!_takePath.empty()) store::remove(_takePath);
        _takePath.clear();
        _haveTake = false;
    }

    voice::WavFileSink* _sink = nullptr;
    std::string _takePath;
    std::string _speechPath;
    uint8_t _speechParts = 0;     // files on disk for the last reply (for replay / cleanup)
    uint8_t _partsQueued = 0;     // parts already handed to voice:: for this reply
    uint8_t _skipLogged = 0;      // last 'ready' count a skip was logged for
    bool _speechCancelled = false;
    std::string _reply;
    std::string _err;
    std::string _sendContext;
    int _scroll = 0;
    int _controlSel = 0;
    bool _controlMode = false;
    bool _confirm = false;  // approval question is on screen
    bool _haveTake = false;
    bool _sending = false;
    bool _discard = false;        // ESC cancelled the in-flight talk job; drop its result
    uint32_t _sendAt = 0;
    uint32_t _waitSince = 0;
    uint32_t _thinkAt = 0;
    uint32_t _lastWorkerPaint = 0;
};

}  // namespace

App* makeComm() { return new CommApp(); }

}  // namespace apps
}  // namespace maz
