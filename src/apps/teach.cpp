#include <algorithm>
#include <string>
#include <vector>

#include "../audio/sfx.h"
#include "../core/notify.h"
#include "../core/shell.h"
#include "../net/host_worker.h"
#include "../storage/store.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

enum class TeachView : uint8_t { Loading, Displays, Starting, Recording, Processing, Result, Error };

class TeachApp final : public App {
public:
    const char* id() const override { return "teach"; }
    const char* title() const override { return "TEACH DEMO"; }
    const char* hints() const override {
        switch (_view) {
            case TeachView::Displays: return "UP/DOWN screen   ENTER start";
            case TeachView::Recording: return "M mark important   ENTER stop";
            case TeachView::Result: return "ENTER save transcript   N new";
            case TeachView::Error: return "R retry";
            default: return "hub working   ESC safe";
        }
    }

    void onEnter() override {
        if (_view == TeachView::Recording || _view == TeachView::Processing) return;
        if (consume()) return;
        loadDisplays();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_view == TeachView::Displays) {
            if (_cursor.onKey(e, static_cast<int>(_displays.size()))) {
                sfx::select(); invalidate(); return true;
            }
            if (e.code == KEY_ENTER && !_displays.empty()) { start(); return true; }
            return false;
        }
        if (_view == TeachView::Recording) {
            if (e.code == KEY_M) { mark(); return true; }
            if (e.code == KEY_ENTER) { stop(); return true; }
            return false;
        }
        if (_view == TeachView::Result) {
            if (e.code == KEY_N) { reset(); loadDisplays(); return true; }
            if (e.code == KEY_ENTER) { save(); return true; }
            if (e.code == KEY_DOWN) { ++_scroll; invalidate(); return true; }
            if (e.code == KEY_UP && _scroll > 0) { --_scroll; invalidate(); return true; }
            return false;
        }
        if (_view == TeachView::Error && e.code == KEY_R) { reset(); loadDisplays(); return true; }
        return false;
    }

    void update() override {
        if (host_worker::jobKind() == host_worker::JobKind::Teach &&
            host_worker::state() == host_worker::State::Done) consume();
        if ((_view == TeachView::Loading || _view == TeachView::Starting ||
             _view == TeachView::Processing) && millis() - _lastPaint > 250) {
            _lastPaint = millis(); invalidate();
        }
        if (_view == TeachView::Recording && millis() - _lastPaint > 500) {
            _lastPaint = millis(); invalidate();
        }
    }

    std::string contextSnapshot() const override {
        if (_view == TeachView::Recording)
            return "Teach-by-Demonstration is actively recording the selected hub display; marks " + std::to_string(_marks);
        if (!_transcript.empty()) return "Teach demo transcript: " + _transcript.substr(0, 180);
        return "Teach-by-Demonstration / selected hub screen workflow recorder";
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "TEACH DEMO", statusText());
        if (_view == TeachView::Displays) { renderDisplays(g); return; }
        if (_view == TeachView::Recording) { renderRecording(g); return; }
        if (_view == TeachView::Result) { renderResult(g); return; }
        if (_view == TeachView::Error) {
            ui::emptyState(g, "Teach unavailable", ui::ellipsis(_error, 34).c_str());
            return;
        }
        ui::panel(g, 55, BODY_Y + 25, 130, 50);
        g.setTextDatum(middle_center);
        g.setFont(&fonts::Font2);
        g.setTextColor(WARN, PANEL);
        g.drawString(host_worker::stateName(), SCREEN_W / 2, BODY_Y + 44);
        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, PANEL);
        g.drawString(_view == TeachView::Processing ? "transcribe + scene frames" : "hub screen recorder", SCREEN_W / 2, BODY_Y + 63);
        g.setTextDatum(top_left);
    }

private:
    const char* statusText() const {
        switch (_view) {
            case TeachView::Recording: return "RECORDING";
            case TeachView::Processing: return "PROCESSING";
            case TeachView::Result: return "READY";
            case TeachView::Error: return "ERROR";
            default: return "HUB SCREEN";
        }
    }

    void reset() {
        _view = TeachView::Loading; _displays.clear(); _session.clear(); _transcript.clear();
        _error.clear(); _marks = 0; _scroll = 0; _startedAt = 0;
    }

    void loadDisplays() {
        if (host_worker::busy()) {
            _view = TeachView::Error; _error = "hub is busy with another job"; invalidate(); return;
        }
        if (!host_worker::submitTeach(host_worker::TeachKind::Displays)) {
            _view = TeachView::Error; _error = "Could not request hub displays"; invalidate(); return;
        }
        _view = TeachView::Loading; invalidate();
    }

    void start() {
        if (host_worker::busy() || _displays.empty()) return;
        const std::string display = _displays[_cursor.sel].id;
        if (!host_worker::submitTeach(host_worker::TeachKind::Start, "", display)) {
            _view = TeachView::Error; _error = "Could not start recorder"; invalidate(); return;
        }
        _view = TeachView::Starting; invalidate();
    }

    void mark() {
        if (_session.empty() || host_worker::busy()) return;
        if (host_worker::submitTeach(host_worker::TeachKind::Mark, _session, "primary user mark")) {
            ++_marks;
            notify::post(Note::Success, "MARK saved", std::to_string(_marks));
            sfx::confirm();
        }
    }

    void stop() {
        if (_session.empty() || host_worker::busy()) return;
        if (!host_worker::submitTeach(host_worker::TeachKind::Stop, _session)) {
            notify::post(Note::Error, "Could not stop Teach", host_worker::stateName()); return;
        }
        _view = TeachView::Processing; invalidate();
    }

    bool consume() {
        if (host_worker::jobKind() != host_worker::JobKind::Teach ||
            host_worker::state() != host_worker::State::Done) return false;
        host_worker::TeachResult result;
        if (!host_worker::takeTeachResult(result)) return false;

        if (result.kind == host_worker::TeachKind::Displays) {
            _displays = std::move(result.displays);
            if (!result.status.ok || _displays.empty()) {
                _view = TeachView::Error;
                _error = result.status.error.empty() ? "No hub displays found. Is ffmpeg installed?" : result.status.error;
            } else {
                _cursor.sel = _cursor.first = 0;
                _view = TeachView::Displays;
            }
        } else if (result.kind == host_worker::TeachKind::Start) {
            if (!result.status.ok) {
                _view = TeachView::Error; _error = result.status.error;
            } else {
                _session = result.status.sessionId;
                _startedAt = millis();
                _view = TeachView::Recording;
                notify::post(Note::Warn, "Teach recording", "hub screen capture is ON");
                sfx::recStart();
            }
        } else if (result.kind == host_worker::TeachKind::Mark) {
            if (!result.status.ok && !result.status.error.empty())
                notify::post(Note::Warn, "MARK failed", result.status.error);
            _view = TeachView::Recording;
        } else if (result.kind == host_worker::TeachKind::Stop) {
            sfx::recStop();
            if (!result.status.ok) {
                _view = TeachView::Error; _error = result.status.error;
            } else {
                _transcript = result.status.transcript;
                _frames = result.status.frameCount;
                _view = TeachView::Result;
                notify::post(Note::Success, "Teach demo ready", std::to_string(_frames) + " scene frames");
            }
        }
        invalidate();
        return true;
    }

    void renderDisplays(M5Canvas& g) {
        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, BG);
        g.drawString("Choose the hub screen to record", PAD, BODY_Y + 12);
        constexpr int visible = 4;
        if (_cursor.sel >= _cursor.first + visible) _cursor.first = _cursor.sel - visible + 1;
        if (_cursor.sel < _cursor.first) _cursor.first = _cursor.sel;
        for (int row = 0; row < visible; ++row) {
            const int idx = _cursor.first + row;
            if (idx >= static_cast<int>(_displays.size())) break;
            const auto& d = _displays[idx];
            const std::string sub = std::to_string(d.width) + "x" + std::to_string(d.height) + (d.primary ? " PRIMARY" : "");
            ui::listRow(g, row + 2, idx == _cursor.sel, d.name.c_str(), sub.c_str());
        }
    }

    void renderRecording(M5Canvas& g) {
        const uint32_t secs = _startedAt ? (millis() - _startedAt) / 1000 : 0;
        ui::panel(g, 58, BODY_Y + 18, 124, 58);
        g.setTextDatum(middle_center);
        g.setFont(&fonts::Font4);
        g.setTextColor(ACCENT2, PANEL);
        const std::string timer = ui::hhmmss(secs);
        g.drawString(timer.c_str(), SCREEN_W / 2, BODY_Y + 39);
        g.setFont(&fonts::Font0);
        g.setTextColor(TEXT, PANEL);
        g.drawString((std::to_string(_marks) + " MARKS").c_str(), SCREEN_W / 2, BODY_Y + 61);
        g.setTextDatum(top_center);
        g.setTextColor(WARN, BG);
        g.drawString("HUB SCREEN RECORDING ACTIVE", SCREEN_W / 2, BODY_Y + 85);
        g.setTextDatum(top_left);
    }

    void renderResult(M5Canvas& g) {
        g.setFont(&fonts::Font0);
        g.setTextColor(OK, BG);
        g.drawString((std::to_string(_frames) + " SCENE FRAMES / " + std::to_string(_marks) + " MARKS").c_str(), PAD, BODY_Y + 15);
        const std::string text = _transcript.empty() ? "Recording saved. No narration audio was configured/detected." : _transcript;
        constexpr size_t width = 37;
        for (int row = 0; row < 5; ++row) {
            const size_t start = static_cast<size_t>(_scroll + row) * width;
            if (start >= text.size()) break;
            g.setTextColor(TEXT, BG);
            g.drawString(text.substr(start, width).c_str(), PAD, BODY_Y + 34 + row * 14);
        }
    }

    void save() {
        store::Record row;
        row.kind = "inbox";
        row.status = "open";
        row.title = "Teach demonstration";
        row.body = _transcript.empty() ? "Screen recording saved on hub" : _transcript;
        row.source = "teach";
        row.ref = _session;
        if (store::addRecord(row)) notify::post(Note::Success, "Saved to Inbox", "Teach demo transcript");
    }

    TeachView _view = TeachView::Loading;
    std::vector<host::TeachDisplay> _displays;
    ListCursor _cursor;
    std::string _session;
    std::string _transcript;
    std::string _error;
    int _marks = 0;
    int _frames = 0;
    int _scroll = 0;
    uint32_t _startedAt = 0;
    uint32_t _lastPaint = 0;
};

struct CaptureItem { const char* label; const char* sub; const char* target; };
constexpr CaptureItem CAPTURE_ITEMS[] = {
    {"TEACH DEMO", "record hub workflow", "teach"},
    {"BRAIN DUMP", "voice -> structured", "braindump"},
    {"VOICE RECORDER", "save a WAV", "recorder"},
};

class CaptureHub final : public App {
public:
    const char* id() const override { return "capturehub"; }
    const char* title() const override { return "CAPTURE"; }
    const char* hints() const override { return "UP/DOWN choose   ENTER open"; }
    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_cursor.onKey(e, 3)) { sfx::select(); invalidate(); return true; }
        if (e.code == KEY_ENTER) { sfx::confirm(); shell::pushById(CAPTURE_ITEMS[_cursor.sel].target); return true; }
        return false;
    }
    void render(M5Canvas& g) override {
        g.fillScreen(BG); ui::header(g, "CAPTURE", "VOICE + SCREEN");
        for (int i = 0; i < 3; ++i)
            ui::listRow(g, i + 2, i == _cursor.sel, CAPTURE_ITEMS[i].label, CAPTURE_ITEMS[i].sub);
    }
private:
    ListCursor _cursor;
};

}  // namespace

App* makeTeach() { return new TeachApp(); }
App* makeCaptureHub() { return new CaptureHub(); }

}  // namespace apps
}  // namespace maz
