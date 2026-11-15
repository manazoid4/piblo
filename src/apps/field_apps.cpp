// v0.7 FIELD screens: laptop snapshot, Beam inbox/outbox and shift clock.
// They are hidden utilities inside the six existing product surfaces; no new
// top-level Home category is introduced.
#include <qrcode.h>

#include <algorithm>
#include <string>
#include <vector>

#include "../audio/sfx.h"
#include "../core/field.h"
#include "../core/notify.h"
#include "../core/settings.h"
#include "../core/sys.h"
#include "../net/host_worker.h"
#include "../storage/store.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

std::string pct(int value) {
    return value < 0 ? "--" : std::to_string(value) + "%";
}

class LaptopApp : public App {
public:
    const char* id() const override { return "laptop"; }
    const char* title() const override { return "PC"; }
    const char* hints() const override { return "R refresh   Fn+F FIELD"; }

    void onEnter() override {
        field::requestSystemStatus();
        _requestedAt = millis();
        invalidate();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (e.code == KEY_R) {
            field::requestSystemStatus();
            _requestedAt = millis();
            sfx::select();
            invalidate();
            return true;
        }
        return false;
    }

    void update() override {
        const uint32_t cadence = Cfg.fieldMode ? 20000u : 10000u;
        if (millis() - _requestedAt > cadence) {
            _requestedAt = millis();
            field::requestSystemStatus();
        }
        if (millis() - _lastPaint > 500) {
            _lastPaint = millis();
            invalidate();
        }
    }

    std::string contextSnapshot() const override {
        if (!Sys.laptopStatusOk) return "hub status unavailable";
        std::string s = "CPU " + pct(Sys.laptopCpuPct) + ", RAM " + pct(Sys.laptopRamPct);
        if (Sys.laptopGpuAvailable)
            s += ", GPU " + pct(Sys.laptopGpuPct) + ", VRAM " +
                 std::to_string(Sys.laptopVramUsedMb) + "/" +
                 std::to_string(Sys.laptopVramTotalMb) + " MB";
        if (Sys.ollamaLoaded) s += ", Ollama " + Sys.ollamaModel;
        return s;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "PC", Cfg.fieldMode ? "FIELD" : "ON DEMAND");
        g.setFont(&fonts::Font0);
        g.setTextDatum(top_left);

        if (!Sys.laptopStatusOk) {
            const bool loading = host_worker::jobKind() == host_worker::JobKind::SystemStatus &&
                                 host_worker::busy();
            ui::emptyState(g, loading ? "Reading hub..." : "PC unavailable",
                           loading ? "one lightweight snapshot" : "R retry / hub may be offline");
            return;
        }

        auto row = [&](int y, const char* name, const std::string& value, uint16_t color = TEXT) {
            g.setTextColor(DIM, BG);
            g.drawString(name, PAD, y);
            g.setTextDatum(top_right);
            g.setTextColor(color, BG);
            g.drawString(value.c_str(), SCREEN_W - PAD, y);
            g.setTextDatum(top_left);
        };
        row(BELOW_RULE, "CPU", pct(Sys.laptopCpuPct));
        row(BODY_Y + 31, "RAM", pct(Sys.laptopRamPct));
        if (Sys.laptopGpuAvailable) {
            row(BODY_Y + 44, "GPU", pct(Sys.laptopGpuPct) + "  " + std::to_string(Sys.laptopGpuTempC) + "C");
            row(BODY_Y + 57, "VRAM", std::to_string(Sys.laptopVramUsedMb) + "/" +
                                    std::to_string(Sys.laptopVramTotalMb) + " MB",
                Sys.laptopVramTotalMb && Sys.laptopVramUsedMb * 100 / Sys.laptopVramTotalMb > 85 ? WARN : TEXT);
        } else {
            row(BODY_Y + 44, "GPU", "not reported", DIM);
            row(BODY_Y + 57, "VRAM", "--", DIM);
        }
        std::string batt = Sys.laptopBatteryPct < 0 ? "AC/desktop" : pct(Sys.laptopBatteryPct);
        if (Sys.laptopCharging) batt += " +";
        row(BODY_Y + 70, "POWER", batt);

        std::string model = Sys.ollamaLoaded ? ui::ellipsis(Sys.ollamaModel, 18) :
                            (Sys.ollamaOnline ? "idle" : "offline");
        row(BODY_Y + 83, "OLLAMA", model, Sys.ollamaLoaded ? ACCENT2 : DIM);
        if (Sys.ollamaLoaded)
            row(BODY_Y + 96, "AI MEM", std::to_string(Sys.ollamaVramMb) + " MB / ctx " +
                                      std::to_string(Sys.ollamaContext));
    }

private:
    uint32_t _requestedAt = 0;
    uint32_t _lastPaint = 0;
};

class BeamApp : public App {
public:
    const char* id() const override { return "beam"; }
    const char* title() const override { return "Beam"; }
    const char* hints() const override {
        if (_qr) return "any key back";
        if (_editing) return "type   ENTER queue   ESC cancel";
        return "N new   Q QR   S save   UP/DOWN";
    }

    void onEnter() override {
        markSeen();
        reload();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_qr) {
            _qr = false;
            invalidate();
            return true;
        }
        if (_editing) {
            if (e.code == KEY_ESC) {
                _editing = false;
                _field.text.clear();
                invalidate();
                return true;
            }
            if (e.code == KEY_ENTER) {
                if (!_field.text.empty()) {
                    field::queueBeam(_field.text);
                    _field.text.clear();
                    _editing = false;
                    reload();
                }
                return true;
            }
            if (_field.onKey(e)) { invalidate(); return true; }
            return false;
        }

        if (_cursor.onKey(e, static_cast<int>(_rows.size()))) {
            sfx::select();
            invalidate();
            return true;
        }
        if (e.code == KEY_N) {
            _editing = true;
            _field.text.clear();
            invalidate();
            return true;
        }
        if (!_rows.empty() && e.code == KEY_Q) {
            _qr = true;
            invalidate();
            return true;
        }
        if (!_rows.empty() && e.code == KEY_S) {
            const auto& selected = _rows[_cursor.sel];
            store::Record copy;
            copy.kind = "inbox";
            copy.status = "open";
            copy.title = selected.title;
            copy.body = selected.body;
            copy.source = "beam";
            copy.ref = selected.ref;
            if (store::addRecord(copy)) notify::post(Note::Success, "Beam saved", "Recall / Inbox");
            return true;
        }
        return false;
    }

    std::string contextSnapshot() const override {
        if (_editing) return "Composing a Beam to the hub";
        if (_rows.empty()) return "Beam inbox empty";
        return _rows[_cursor.sel].title + ": " + _rows[_cursor.sel].body.substr(0, 180);
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        if (_qr) { renderQr(g); return; }
        static char sub[16];
        snprintf(sub, sizeof(sub), "%u QUEUED", Sys.outboxQueued);
        ui::header(g, "BEAM", host_worker::busy() ? host_worker::stateName()
                              : Sys.outboxQueued ? sub : "TEXT / LINK");
        if (_editing) {
            g.setFont(&fonts::Font0);
            g.setTextColor(DIM, BG);
            g.drawString("SEND TO PC / queues offline", PAD, BODY_Y + 22);
            _field.draw(g, PAD, BODY_Y + 40, SCREEN_W - PAD * 2, "text or https://...");
            return;
        }
        if (_rows.empty()) {
            ui::emptyState(g, "No Beam received", "N sends text to the hub");
            return;
        }
        const auto& r = _rows[_cursor.sel];
        g.setFont(&fonts::Font2);
        g.setTextColor(ACCENT, BG);
        g.drawString(ui::ellipsis(r.title, 24).c_str(), PAD, BODY_Y + 20);
        g.setFont(&fonts::Font0);
        g.setTextColor(TEXT, BG);
        for (int row = 0; row < 4; ++row) {
            const size_t start = static_cast<size_t>(row) * 37;
            if (start >= r.body.size()) break;
            g.drawString(r.body.substr(start, 37).c_str(), PAD, BODY_Y + 40 + row * 13);
        }
        g.setTextDatum(top_right);
        g.setTextColor(DIM, BG);
        g.drawString((std::to_string(_cursor.sel + 1) + "/" + std::to_string(_rows.size())).c_str(),
                     SCREEN_W - PAD, BODY_Y + 93);
        g.setTextDatum(top_left);
    }

private:
    void reload() {
        _rows = store::loadRecords("beam", 32);
        _cursor.clamp(static_cast<int>(_rows.size()));
        invalidate();
    }

    void markSeen() {
        for (auto r : store::loadRecords("beam", 64)) {
            if (r.status == "open") {
                r.status = "seen";
                store::updateRecord(r);
            }
        }
        Sys.beamUnread = 0;
    }

    void renderQr(M5Canvas& g) {
        if (_rows.empty()) return;
        const std::string& text = _rows[_cursor.sel].body;
        if (text.size() > 100) {
            ui::emptyState(g, "Too long for quick QR", "S saves it to Recall instead");
            return;
        }
        QRCode qr;
        uint8_t buf[qrcode_getBufferSize(5)];
        if (qrcode_initText(&qr, buf, 5, ECC_LOW, text.c_str()) < 0) {
            ui::emptyState(g, "Could not encode", "shorten the Beam");
            return;
        }
        const int scale = std::max(1, (SCREEN_H - 12) / qr.size);
        const int side = qr.size * scale;
        const int ox = (SCREEN_W - side) / 2;
        const int oy = (SCREEN_H - side) / 2;
        g.fillScreen(TFT_WHITE);
        for (uint8_t y = 0; y < qr.size; ++y)
            for (uint8_t x = 0; x < qr.size; ++x)
                if (qrcode_getModule(&qr, x, y))
                    g.fillRect(ox + x * scale, oy + y * scale, scale, scale, TFT_BLACK);
    }

    std::vector<store::Record> _rows;
    ListCursor _cursor;
    TextField _field;
    bool _editing = false;
    bool _qr = false;
};

class ShiftApp : public App {
public:
    const char* id() const override { return "shift"; }
    const char* title() const override { return "Shift Clock"; }
    const char* hints() const override { return "ENTER start/stop   Fn+F FIELD"; }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (e.code == KEY_ENTER) {
            field::toggleShift();
            sfx::confirm();
            invalidate();
            return true;
        }
        return false;
    }

    void update() override {
        if (Sys.shiftRunning && millis() - _lastPaint > 500) {
            _lastPaint = millis();
            invalidate();
        }
    }

    std::string contextSnapshot() const override {
        return std::string(Sys.shiftRunning ? "Active shift " : "Shift stopped at ") +
               field::shiftElapsedText();
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "SHIFT CLOCK", Cfg.fieldMode ? "FIELD" : "NORMAL");
        g.setTextDatum(middle_center);
        g.setFont(&fonts::Font4);
        g.setTextColor(Sys.shiftRunning ? ACCENT2 : DIM, BG);
        g.drawString(field::shiftElapsedText().c_str(), SCREEN_W / 2, BODY_Y + 49);
        g.setFont(&fonts::Font0);
        g.setTextColor(Sys.shiftRunning ? OK : DIM, BG);
        g.drawString(Sys.shiftRunning ? "ON SHIFT" : "ENTER TO START", SCREEN_W / 2, BODY_Y + 78);
        g.setTextDatum(top_left);
    }

private:
    uint32_t _lastPaint = 0;
};

}  // namespace

App* makeLaptop() { return new LaptopApp(); }
App* makeBeam() { return new BeamApp(); }
App* makeShift() { return new ShiftApp(); }

}  // namespace apps
}  // namespace maz
