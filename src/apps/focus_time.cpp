// Focus and Stopwatch.
//
// Focus is a commitment ("I am working on FlowLens for 25 minutes") and lives
// in the shell so it survives leaving the screen. Stopwatch is a measurement
// and is deliberately separate — merging them would make both worse.
#include "../audio/sfx.h"
#include "../core/notify.h"
#include "../core/settings.h"
#include "../core/shell.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

// 10/25/45/60 plus a 5-minute breather. A custom-duration entry screen is the
// classic feature nobody uses on a device this size.
const uint32_t PRESETS[] = {600, 1500, 2700, 3600, 300};
constexpr int  PRESET_N  = sizeof(PRESETS) / sizeof(PRESETS[0]);

// ------------------------------------------------------------------ Focus
class FocusApp : public App {
public:
    const char* id() const override { return "focus"; }
    const char* title() const override { return "Focus"; }

    const char* hints() const override {
        if (shell::focus::running())
            return shell::focus::paused() ? "ENTER resume   C cancel"
                                          : "ENTER pause   C cancel";
        if (_labelling) return "ENTER start  ESC skip";
        return "UP/DOWN preset  ENTER start";
    }

    void onEnter() override { invalidate(); }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;

        if (shell::focus::running()) {
            if (e.code == KEY_ENTER) {
                if (shell::focus::paused()) shell::focus::resume();
                else shell::focus::pause();
                invalidate();
                return true;
            }
            if (e.code == KEY_C) {
                shell::focus::cancel();
                notify::post(Note::Info, "Focus cancelled");
                invalidate();
                return true;
            }
            return false;
        }

        if (_labelling) {
            if (e.code == KEY_ENTER || e.code == KEY_ESC) {
                begin();
                return true;
            }
            if (_field.onKey(e)) {
                invalidate();
                return true;
            }
            return false;
        }

        if (e.code == KEY_UP) {
            step(-1);
            return true;
        }
        if (e.code == KEY_DOWN) {
            step(+1);
            return true;
        }
        if (e.code == KEY_ENTER) {
            _labelling = true;
            _field.text.clear();
            _field.limit = 24;
            invalidate();
            return true;
        }
        return false;
    }

    void update() override {
        if (shell::focus::running()) invalidate();
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);

        if (shell::focus::running()) {
            ui::header(g, shell::focus::paused() ? "Focus / paused" : "Focus");
            const std::string& lbl = shell::focus::label();
            if (!lbl.empty()) {
                g.setFont(&fonts::Font2);
                g.setTextDatum(top_center);
                g.setTextColor(ACCENT, BG);
                g.drawString(ui::ellipsis(lbl, 24).c_str(), SCREEN_W / 2,
                             BODY_Y + 22);
                g.setTextDatum(top_left);
            }
            g.setTextDatum(middle_center);
            g.setFont(&fonts::Font7);
            g.setTextColor(shell::focus::paused() ? DIM : TEXT, BG);
            g.drawString(ui::hhmmss(shell::focus::remaining()).c_str(),
                         SCREEN_W / 2, BODY_Y + 58);
            g.setTextDatum(top_left);

            const uint32_t total = shell::focus::total();
            const float    done =
                total ? 1.f - (shell::focus::remaining() / (float)total) : 0.f;
            ui::progress(g, PAD, BODY_Y + 84, SCREEN_W - PAD * 2, 5, done,
                         ACCENT);
            return;
        }

        if (_labelling) {
            ui::header(g, "Focus");
            g.setFont(&fonts::Font0);
            g.setTextColor(DIM, BG);
            g.setTextDatum(top_left);
            char sub[40];
            snprintf(sub, sizeof(sub), "%u minutes - what on?",
                     (unsigned)(PRESETS[_preset] / 60));
            g.drawString(sub, PAD, BODY_Y + 24);
            _field.draw(g, PAD, BODY_Y + 38, SCREEN_W - PAD * 2, "FlowLens");
            return;
        }

        ui::header(g, "Focus");
        char big[8];
        snprintf(big, sizeof(big), "%u", (unsigned)(PRESETS[_preset] / 60));
        ui::bigValue(g, big, "minutes - ENTER to start", ACCENT);
    }

private:
    void step(int d) {
        _preset = (_preset + d + PRESET_N) % PRESET_N;
        sfx::select();
        invalidate();
    }

    void begin() {
        shell::focus::start(PRESETS[_preset], _field.text);
        _labelling = false;
        sfx::confirm();
        notify::post(Note::Info, "Focus started",
                     _field.text.empty() ? "clock is running" : _field.text);
        invalidate();
    }

    int       _preset    = 1;  // 25 minutes
    bool      _labelling = false;
    TextField _field;
};

// -------------------------------------------------------------- Stopwatch
class StopwatchApp : public App {
public:
    const char* id() const override { return "stopwatch"; }
    const char* title() const override { return "Stopwatch"; }
    const char* hints() const override {
        return _running ? "ENTER stop   L lap   R reset" : "ENTER start   R reset";
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (e.code == KEY_ENTER) {
            if (_running) {
                _accum += millis() - _startedAt;
                _running = false;
            } else {
                _startedAt = millis();
                _running   = true;
            }
            sfx::confirm();
            invalidate();
            return true;
        }
        if (e.code == KEY_R) {
            _running = false;
            _accum   = 0;
            _laps    = 0;
            invalidate();
            return true;
        }
        if (e.code == KEY_L && _running && _laps < MAX_LAPS) {
            _lap[_laps++] = elapsedMs();
            sfx::select();
            invalidate();
            return true;
        }
        return false;
    }

    void update() override {
        if (_running) invalidate();
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "Stopwatch", _running ? "running" : "stopped");

        const uint32_t ms = elapsedMs();
        char           buf[24];
        snprintf(buf, sizeof(buf), "%02u:%02u.%u", (unsigned)(ms / 60000),
                 (unsigned)((ms / 1000) % 60), (unsigned)((ms % 1000) / 100));
        g.setTextDatum(middle_center);
        g.setFont(&fonts::Font7);
        g.setTextColor(_running ? TEXT : DIM, BG);
        g.drawString(buf, SCREEN_W / 2, BODY_Y + 40);
        g.setTextDatum(top_left);

        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, BG);
        for (int i = 0; i < _laps && i < 3; ++i) {
            const uint32_t l = _lap[_laps - 1 - i];
            snprintf(buf, sizeof(buf), "%u  %02u:%02u.%u",
                     (unsigned)(_laps - i), (unsigned)(l / 60000),
                     (unsigned)((l / 1000) % 60), (unsigned)((l % 1000) / 100));
            g.drawString(buf, PAD, BODY_Y + 70 + i * 11);
        }
    }

private:
    uint32_t elapsedMs() const {
        return _accum + (_running ? millis() - _startedAt : 0);
    }

    static constexpr int MAX_LAPS = 12;
    uint32_t             _accum         = 0;
    uint32_t             _startedAt     = 0;
    bool                 _running       = false;
    uint32_t             _lap[MAX_LAPS] = {0};
    int                  _laps          = 0;
};

}  // namespace

App* makeFocus() { return new FocusApp(); }
App* makeStopwatch() { return new StopwatchApp(); }

}  // namespace apps
}  // namespace maz
