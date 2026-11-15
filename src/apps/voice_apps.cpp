// Call, Capture and Recorder — the three voice surfaces.
//
// They share one recording engine (audio/voice) and differ only in intent:
//   Call     — a conversation turn. Short cap, review then keep or bin.
//              This is the screen v0.2 rewires to OpenFlowKit.
//   Capture  — a thought. Fastest path from pocket to stored, voice or text.
//   Recorder — a session. Long-form, file management, playback.
#include <algorithm>
#include <time.h>
#include <vector>

#include "../audio/sfx.h"
#include "../audio/voice.h"
#include "../core/notify.h"
#include "../core/shell.h"
#include "../core/settings.h"
#include "../core/sys.h"
#include "../input/keyboard.h"
#include "../net/mazhost.h"
#include "../storage/store.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

const char* stateName(voice::State s) {
    switch (s) {
        case voice::State::Listening: return "LISTENING";
        case voice::State::Paused:    return "PAUSED";
        case voice::State::Saving:    return "SAVING";
        case voice::State::Playing:   return "PLAYING";
        case voice::State::Error:     return "ERROR";
        default:                      return "READY";
    }
}

uint16_t stateColour(voice::State s) {
    switch (s) {
        case voice::State::Listening: return LIVE;
        case voice::State::Playing:   return TEXT;
        case voice::State::Error:     return ERR;
        default:                      return DIM;
    }
}

// The shared voice face: state word, the MAZ mark breathing with input level,
// and a clipping warning. Every voice screen looks like this so the device
// feels like one thing.
void drawVoiceFace(M5Canvas& g, voice::State st, const char* caption) {
    const bool live = st == voice::State::Listening;
    ui::mark(g, SCREEN_W / 2, BODY_Y + 42, 16,
             live ? LIVE : ACCENT,
             live ? voice::level() : 0.f);

    g.setTextDatum(top_center);
    g.setFont(&fonts::Font2);
    g.setTextColor(stateColour(st), BG);
    g.drawString(stateName(st), SCREEN_W / 2, BODY_Y + 66);

    g.setFont(&fonts::Font0);
    g.setTextColor(DIM, BG);
    if (live && voice::clipped()) {
        g.setTextColor(WARN, BG);
        g.drawString("too loud - back off the mic", SCREEN_W / 2, BODY_Y + 86);
    } else if (caption && *caption) {
        g.drawString(caption, SCREEN_W / 2, BODY_Y + 86);
    }
    g.setTextDatum(top_left);
}

void drawShortText(M5Canvas& g, const std::string& text, int y, int lines = 4) {
    g.setFont(&fonts::Font0);
    g.setTextColor(TEXT, BG);
    g.setTextDatum(top_left);
    constexpr size_t width = 38;
    for (int line = 0; line < lines; ++line) {
        const size_t start = line * width;
        if (start >= text.size()) break;
        g.drawString(text.substr(start, width).c_str(), PAD, y + line * 14);
    }
}

// ---------------------------------------------------------------- Capture
class CaptureApp : public App {
public:
    const char* id() const override { return "braindump"; }
    const char* title() const override { return "Brain Dump"; }

    const char* hints() const override {
        if (voice::state() == voice::State::Listening)
            return "H mark  P pause  ENTER done";
        if (voice::state() == voice::State::Paused) return "P resume  ENTER done";
        if (_processing) return "saving...";
        if (!_result.empty()) return _resultOk ? "R again  P play  I inbox" : "O retry  R again  I inbox";
        if (_ready) return "O retry  R again  P play raw";
        return "recording saved";
    }

    void onEnter() override {
        _highlights.clear();
        _ready = false;
        _result.clear();
        _scroll = 0;
        _held = KB.held(KEY_SPACE);  // entered by holding SPACE on Home: release = stop + send
        beginVoice();
        invalidate();
    }

    void onExit() override {
        if (voice::state() == voice::State::Listening || voice::state() == voice::State::Paused) endVoice();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) {
            if (e.code == KEY_SPACE && _held && voice::state() == voice::State::Listening) { _held = false; endVoice(); return true; }
            return false;
        }
        if (e.code == KEY_H && voice::state() == voice::State::Listening) {
            _highlights.push_back(voice::elapsedSeconds());
            notify::post(Note::Success, "Highlighted", ui::hhmmss(voice::elapsedSeconds()));
            return true;
        }
        if (e.code == KEY_P && voice::state() == voice::State::Listening) { voice::pause(); invalidate(); return true; }
        if (e.code == KEY_P && voice::state() == voice::State::Paused) { voice::resume(); invalidate(); return true; }
        if (e.code == KEY_ENTER && (voice::state() == voice::State::Listening || voice::state() == voice::State::Paused)) { endVoice(); return true; }
        if (e.code == KEY_P && _ready) { voice::play(_path); return true; }
        if (e.code == KEY_O && _ready && !_processing) {
            // Manual retry, for when the laptop was not there the first time.
            _processing = true; _processAt = millis() + 180; invalidate(); return true;
        }
        // One more thought, without walking back out to Home for it.
        if (e.code == KEY_R && !_processing &&
            voice::state() != voice::State::Listening) {
            onEnter();
            return true;
        }
        if (e.code == KEY_I && !_result.empty()) {
            shell::pushById("inbox");
            return true;
        }
        if (!_result.empty()) {
            if (e.code == KEY_DOWN) { _scroll++; invalidate(); return true; }
            if (e.code == KEY_UP)   { if (_scroll) _scroll--; invalidate(); return true; }
        }
        return false;
    }

    void update() override {
        if (_processing && millis() >= _processAt) process();
        if (voice::state() == voice::State::Listening) invalidate();
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "Brain Dump", store::backendName());

        if (voice::state() == voice::State::Listening ||
            voice::state() == voice::State::Paused) {
            // The mark counts your marks: a highlight you cannot see landing is
            // a highlight you stop trusting mid-thought.
            std::string caption = ui::hhmmss(voice::elapsedSeconds());
            if (!_highlights.empty())
                caption += "   " + std::to_string(_highlights.size()) + " marked";
            drawVoiceFace(g, voice::state(), caption.c_str());
            return;
        }

        if (!_result.empty()) {
            // Show what came back, on the device, rather than making you walk
            // to Inbox to find out whether it worked.
            g.setFont(&fonts::Font0);
            g.setTextColor(DIM, BG);
            g.drawString(_resultOk ? "SAVED" : "NOT SENT - PRESS O TO RETRY",
                         PAD, BODY_Y + 22);
            drawShortText(g, _result.substr(std::min(_result.size(),
                                                     _scroll * size_t(38))),
                          BODY_Y + 36, 5);
            return;
        }

        ui::emptyState(
            g,
            _processing ? "SAVING" : (_ready ? "Raw thought kept" : "Ready"),
            _processing ? "raw audio stays on the device"
                        : (_ready ? "R records another" : "recording starts immediately"));
    }

private:
    void beginVoice() {
        _sink = new voice::WavFileSink("braindumps");
        // Five minutes, not two: a brain dump is the one place on this device
        // where being cut off mid-thought defeats the point. 16kHz mono is
        // ~1.9MB/min, so this is still a small file.
        if (!voice::start(_sink, 300)) {
            notify::post(Note::Error, "Cannot record", voice::lastError());
            delete _sink;
            _sink = nullptr;
            return;
        }
        sfx::recStart();
        _queued = false;
        invalidate();
    }

    void endVoice() {
        const bool   ok    = voice::stop();
        const size_t bytes = _sink ? _sink->samples() * 2 : 0;
        if (_sink) _path = _sink->path();
        sfx::recStop();
        if (ok)
            notify::post(Note::Success, "BrainDump saved", ui::humanSize(bytes));
        else
            notify::post(Note::Error, "Nothing captured", voice::lastError());
        delete _sink;
        _sink = nullptr;
        _ready = ok;
        // Finishing a thought is the whole intent; making you press O as well
        // just meant a queue of unprocessed dumps nobody remembered to send.
        // The raw WAV is kept either way, so this can never lose the capture.
        if (ok) {
            _processing = true;
            _processAt  = millis() + 180;
        }
        invalidate();
    }

    void process() {
        _processing = false;
        const auto result = host::brainDump(_path, _highlights);
        if (!result.ok && _queued) {  // retry while still offline: no second outbox record
            notify::post(Note::Warn, "Still offline", "raw audio kept");
            invalidate();
            return;
        }
        store::Record item;
        item.kind = result.ok ? "inbox" : "outbox";
        item.status = result.ok ? "open" : "queued";
        item.title = result.ok ? "Brain Dump processed" : "BrainDump queued";
        item.body = result.ok ? result.text : result.error;
        item.source = "braindump"; item.ref = _path;
        store::addRecord(item);
        _queued = !result.ok;
        _resultOk = result.ok;
        _result   = result.ok ? result.text : result.error;
        if (_result.empty())
            _result = result.ok ? "(nothing came back)" : "raw audio kept on device";
        _scroll = 0;
        notify::post(result.ok ? Note::Success : Note::Warn,
                     result.ok ? "Useful output ready" : "Queued offline",
                     result.ok ? result.provider : "raw audio kept");
        invalidate();
    }

    voice::WavFileSink* _sink = nullptr;
    std::string         _path;
    std::vector<uint32_t> _highlights;
    std::string         _result;
    bool                _resultOk = false;
    size_t              _scroll = 0;
    bool                _ready = false;
    bool                _processing = false;
    bool                _queued = false;
    bool                _held = false;
    uint32_t            _processAt = 0;
};

// --------------------------------------------------------------- Recorder
class RecorderApp : public App {
public:
    const char* id() const override { return "recorder"; }
    const char* title() const override { return "Recorder"; }

    const char* hints() const override {
        if (voice::state() == voice::State::Listening) return "ENTER stop & save";
        if (_files.empty()) return "ENTER record";
        return "ENTER record   P play   D delete";
    }

    void onEnter() override {
        reload();
        invalidate();
    }
    void onExit() override {
        voice::stopPlayback();
        if (voice::state() == voice::State::Listening) stopRecording();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;

        if (e.code == KEY_ENTER) {
            if (voice::state() == voice::State::Listening) stopRecording();
            else startRecording();
            return true;
        }
        if (voice::state() == voice::State::Listening) return false;

        if (_cursor.onKey(e, static_cast<int>(_files.size()))) {
            invalidate();
            return true;
        }
        if (_files.empty()) return false;

        if (e.code == KEY_P) {
            if (voice::isPlaying()) voice::stopPlayback();
            else voice::play(_files[_cursor.sel].path);
            invalidate();
            return true;
        }
        if (e.code == KEY_D) {
            // Two-step delete: the second D within the confirm window is the
            // one that acts. No modal, no accidental loss.
            if (_confirmDelete && millis() - _confirmAt < 3000) {
                store::remove(_files[_cursor.sel].path);
                notify::post(Note::Info, "Deleted", _files[_cursor.sel].name);
                _confirmDelete = false;
                reload();
            } else {
                _confirmDelete = true;
                _confirmAt     = millis();
                notify::post(Note::Warn, "Press D again", "to delete");
            }
            invalidate();
            return true;
        }
        return false;
    }

    void update() override {
        if (voice::state() == voice::State::Listening || voice::isPlaying())
            invalidate();
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);

        if (voice::state() == voice::State::Listening) {
            ui::header(g, "Recording", store::backendName());
            drawVoiceFace(g, voice::state(),
                          ui::hhmmss(voice::elapsedSeconds()).c_str());
            return;
        }

        char right[24];
        snprintf(right, sizeof(right), "%u files", (unsigned)_files.size());
        ui::header(g, "Recorder", right);

        if (_files.empty()) {
            ui::emptyState(g, "No recordings", "ENTER to start one");
            return;
        }

        const int rows = std::min<int>(theme::ROWS_VISIBLE - 1,
                                       static_cast<int>(_files.size()));
        for (int i = 0; i < rows; ++i) {
            const int idx = _cursor.first + i;
            if (idx >= static_cast<int>(_files.size())) break;
            ui::listRow(g, i + 1, idx == _cursor.sel,
                        ui::ellipsis(_files[idx].name, 20).c_str(),
                        ui::humanSize(_files[idx].size).c_str());
        }
        ui::scrollBar(g, static_cast<int>(_files.size()), _cursor.first,
                      theme::ROWS_VISIBLE - 1);
    }

private:
    void reload() {
        _files = store::list("recordings", ".wav");
        _cursor.clamp(static_cast<int>(_files.size()));
    }

    void startRecording() {
        if (!store::ready()) {
            notify::post(Note::Error, "No storage", "insert an SD card");
            return;
        }
        _sink = new voice::WavFileSink("recordings");
        if (!voice::start(_sink, MAX_SECONDS)) {
            notify::post(Note::Error, "Cannot record", voice::lastError());
            delete _sink;
            _sink = nullptr;
            return;
        }
        sfx::recStart();
        invalidate();
    }

    void stopRecording() {
        const bool   ok    = voice::stop();
        const size_t bytes = _sink ? _sink->samples() * 2 : 0;
        sfx::recStop();
        if (ok)
            notify::post(Note::Success, "Recording saved", ui::humanSize(bytes));
        else
            notify::post(Note::Error, "Recording failed", voice::lastError());
        delete _sink;
        _sink = nullptr;
        reload();
        invalidate();
    }

    // 30 minutes at 16kHz mono is about 57MB: fine on SD, and the cap stops a
    // forgotten recording from filling the card overnight.
    static constexpr uint32_t MAX_SECONDS = 30 * 60;

    std::vector<store::Entry> _files;
    ListCursor                _cursor;
    voice::WavFileSink*       _sink          = nullptr;
    bool                      _confirmDelete = false;
    uint32_t                  _confirmAt     = 0;
};

}  // namespace

// ---------------------------------------------------------------- Type on PC
// Piblo Flow: hold SPACE, speak, release -> Core cleans the words and types them
// into whatever window is focused on the computer. If focus moved while Core was
// transcribing, Core leaves the text on the PC clipboard instead of typing it.
class PcTypeApp : public App {
public:
    const char* id() const override { return "pctype"; }
    const char* title() const override { return "Type on PC"; }

    const char* hints() const override {
        switch (dictate::state()) {
            case dictate::State::Listening: return _toggle ? "tap SPACE: type  ESC cancel" : "release SPACE: type  ESC cancel";
            case dictate::State::Working:   return "typing on PC...";
            default: return "hold or tap SPACE to speak";
        }
    }

    void onEnter() override {
        _held = KB.held(KEY_SPACE);  // arrived by holding SPACE: start at once
        if (_held) begin();
        invalidate();
    }

    void onExit() override {
        if (dictate::active(this)) dictate::cancel();
    }

    bool onKey(const KeyEvent& e) override {
        const bool mine = dictate::active(this);
        if (e.code == KEY_SPACE) {
            const bool listening = mine && dictate::state() == dictate::State::Listening;
            if (e.down && !mine) {
                begin();
                _downAt = millis();
            } else if (e.down && listening && _toggle) {
                _toggle = false;  // second tap ends a tap-started recording
                dictate::stop();
            } else if (!e.down && listening) {
                // A quick tap keeps recording until the next tap; a real hold ends on release.
                if (millis() - _downAt < 350) _toggle = true;
                else dictate::stop();
            }
            invalidate();
            return true;
        }
        if (e.down && e.code == KEY_ESC && mine) {  // cancel, stay on the screen
            dictate::cancel();
            sfx::recStop();
            _toggle = false;
            _status = "Cancelled";
            _text.clear();
            invalidate();
            return true;
        }
        return false;
    }

    void update() override {
        std::string out;
        if (dictate::take(this, out)) {
            const std::string pc = dictate::pcOutcome();
            _text   = out;
            _ok     = pc == "pasted";
            _status = _ok ? "Typed on PC" : (pc == "clipboard" ? "On PC clipboard - Ctrl+V" : "Sent");
            invalidate();
        } else if (_wasMine && !dictate::active(this) && dictate::state() == dictate::State::Failed) {
            _ok = false;
            _status = dictate::error();
            _text.clear();
            invalidate();
        }
        _wasMine = dictate::active(this);
        if (_wasMine) invalidate();  // live level meter
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "Type on PC", Sys.hostOnline ? "PC" : "OFFLINE");
        const auto st = dictate::state();
        if (dictate::active(this) && st == dictate::State::Listening) {
            drawVoiceFace(g, voice::State::Listening, ui::hhmmss(dictate::elapsedSeconds()).c_str());
            return;
        }
        if (dictate::active(this) && st == dictate::State::Working) {
            ui::emptyState(g, "TYPING", "cleaning up your words");
            return;
        }
        if (_status.empty()) {
            ui::emptyState(g, "Hold SPACE", "speak, release: it types on your PC");
            return;
        }
        g.setFont(&fonts::Font0);
        g.setTextColor(_ok ? LIVE : WARN, BG);
        g.drawString(_status.c_str(), PAD, BODY_Y + 8);
        drawShortText(g, _text, BODY_Y + 24, 6);
    }

private:
    void begin() {
        _toggle = false;
        _status.clear();
        _text.clear();
        if (!Sys.wifiConnected || !host::configured()) {
            _ok = false;
            _status = "Connect to your PC first";
            return;
        }
        if (!dictate::start(this, true)) {
            _ok = false;
            _status = *dictate::error() ? dictate::error() : "Busy - try again";
        }
    }

    bool        _held    = false;
    bool        _toggle  = false;   // started by a tap: next tap ends it
    uint32_t    _downAt  = 0;
    bool        _wasMine = false;
    bool        _ok      = false;
    std::string _status;
    std::string _text;
};

App* makePcType() { return new PcTypeApp(); }
App* makeCapture() { return new CaptureApp(); }
App* makeRecorder() { return new RecorderApp(); }

}  // namespace apps
}  // namespace maz
