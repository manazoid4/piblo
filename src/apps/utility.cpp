// The community-inspired utilities: Calculator, QR, Text Viewer, Generator.
//
// Chosen from the Reddit/forum research in docs/research/COMMUNITY_FEATURES.md
// on one test — does the keyboard make it better than reaching for a phone?
// Anything that was merely possible (packet tools, spam, jammers) was left to
// Bruce, which is still one M5Launcher entry away.
#include <esp_system.h>
#include <qrcode.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

#include "../audio/sfx.h"
#include "../core/notify.h"
#include "../storage/store.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

// ------------------------------------------------------------- Calculator
// A real recursive-descent parser rather than a button grid: on a device with
// a physical number row, typing "1250*0.2" and pressing ENTER is the whole
// interaction, and that is the one thing a phone calculator is worse at.
struct Parser {
    const char* p;
    bool        bad = false;

    void skip() {
        while (*p == ' ') p++;
    }
    double number() {
        skip();
        if (*p == '(') {
            p++;
            const double v = expr();
            skip();
            if (*p == ')') p++;
            else bad = true;
            return v;
        }
        if (*p == '-') {
            p++;
            return -number();
        }
        char*        end = nullptr;
        const double v   = strtod(p, &end);
        if (end == p) {
            bad = true;
            return 0;
        }
        p = end;
        return v;
    }
    double term() {
        double v = number();
        for (;;) {
            skip();
            if (*p == '*') {
                p++;
                v *= number();
            } else if (*p == '/') {
                p++;
                const double d = number();
                if (d == 0) {
                    bad = true;
                    return 0;
                }
                v /= d;
            } else if (*p == '%') {
                p++;
                const double d = number();
                if (d == 0) {
                    bad = true;
                    return 0;
                }
                v = fmod(v, d);
            } else {
                return v;
            }
        }
    }
    double expr() {
        double v = term();
        for (;;) {
            skip();
            if (*p == '+') {
                p++;
                v += term();
            } else if (*p == '-') {
                p++;
                v -= term();
            } else {
                return v;
            }
        }
    }
};

class CalculatorApp : public App {
public:
    const char* id() const override { return "calc"; }
    const char* title() const override { return "Calculator"; }
    const char* hints() const override {
        return "ENTER =  A use answer  del";
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (e.code == KEY_ENTER) {
            evaluate();
            return true;
        }
        if (e.code == KEY_A && !_answer.empty()) {
            _field.text += _answer;
            invalidate();
            return true;
        }
        if (_field.onKey(e)) {
            invalidate();
            return true;
        }
        return false;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "Calculator");
        _field.draw(g, PAD, BODY_Y + 24, SCREEN_W - PAD * 2, "e.g. 1250*0.2");

        g.setTextDatum(top_right);
        g.setFont(&fonts::Font4);
        g.setTextColor(_error ? ERR : ACCENT, BG);
        g.drawString(_error ? "error" : _answer.c_str(), SCREEN_W - PAD,
                     BODY_Y + 50);
        g.setTextDatum(top_left);

        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, BG);
        g.drawString("+ - * / % ( )", PAD, BODY_Y + 78);
    }

private:
    void evaluate() {
        if (_field.text.empty()) return;
        Parser       ps{_field.text.c_str()};
        const double v = ps.expr();
        ps.skip();
        _error = ps.bad || *ps.p != '\0';
        if (_error) {
            sfx::error();
            invalidate();
            return;
        }
        char buf[32];
        // Trim to something a human reads: integers stay integers.
        if (fabs(v - llround(v)) < 1e-9 && fabs(v) < 1e12)
            snprintf(buf, sizeof(buf), "%lld", (long long)llround(v));
        else
            snprintf(buf, sizeof(buf), "%.6g", v);
        _answer = buf;
        sfx::confirm();
        invalidate();
    }

    TextField   _field;
    std::string _answer;
    bool        _error = false;
};

// --------------------------------------------------------------------- QR
// Getting a URL, a Wi-Fi password or an address off the Cardputer and into a
// phone is the one transfer that needs no cable and no app.
class QrApp : public App {
public:
    const char* id() const override { return "qr"; }
    const char* title() const override { return "QR Code"; }
    const char* hints() const override {
        return _showing ? "any key back" : "type   ENTER show";
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_showing) {
            _showing = false;
            invalidate();
            return true;
        }
        if (e.code == KEY_ENTER) {
            if (_field.text.empty()) return true;
            _showing = true;
            sfx::confirm();
            invalidate();
            return true;
        }
        if (_field.onKey(e)) {
            invalidate();
            return true;
        }
        return false;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        if (!_showing) {
            ui::header(g, "QR Code");
            _field.draw(g, PAD, BODY_Y + 26, SCREEN_W - PAD * 2,
                        "https://maz.works");
            g.setFont(&fonts::Font0);
            g.setTextColor(DIM, BG);
            g.setTextDatum(top_left);
            g.drawString("up to ~100 characters", PAD, BODY_Y + 50);
            return;
        }

        // Version 5 with low ECC holds ~106 alphanumeric characters and still
        // scans reliably at 3px per module on a 135px-tall panel.
        QRCode  qr;
        uint8_t buf[qrcode_getBufferSize(5)];
        if (qrcode_initText(&qr, buf, 5, ECC_LOW, _field.text.c_str()) < 0) {
            ui::emptyState(g, "Too long to encode", "shorten the text");
            return;
        }
        const int scale = std::max(1, (SCREEN_H - 12) / qr.size);
        const int side  = qr.size * scale;
        const int ox    = (SCREEN_W - side) / 2;
        const int oy    = (SCREEN_H - side) / 2;

        // Quiet zone in white: scanners need the border, not just the code.
        g.fillRect(ox - 4, oy - 4, side + 8, side + 8, TFT_WHITE);
        for (uint8_t y = 0; y < qr.size; y++)
            for (uint8_t x = 0; x < qr.size; x++)
                if (qrcode_getModule(&qr, x, y))
                    g.fillRect(ox + x * scale, oy + y * scale, scale, scale,
                               TFT_BLACK);
    }

private:
    TextField _field;
    bool      _showing = false;
};

// ------------------------------------------------------------ Text Viewer
// Reading reference material — prompts, instructions, project notes — off the
// card without a laptop.
class ViewerApp : public App {
public:
    const char* id() const override { return "viewer"; }
    const char* title() const override { return "Text Viewer"; }
    const char* hints() const override {
        return _reading ? "up/down scroll   ESC list" : "ENTER open   ESC back";
    }

    void onEnter() override {
        scan();
        invalidate();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_reading) {
            if (e.code == KEY_DOWN) {
                _scroll++;
                invalidate();
                return true;
            }
            if (e.code == KEY_UP) {
                if (_scroll > 0) _scroll--;
                invalidate();
                return true;
            }
            if (e.code == KEY_ESC) {
                _reading = false;
                invalidate();
                return true;
            }
            return false;
        }
        if (_cursor.onKey(e, static_cast<int>(_files.size()))) {
            invalidate();
            return true;
        }
        if (!_files.empty() && e.code == KEY_ENTER) {
            _body    = store::readText(_files[_cursor.sel].path, 6144);
            _scroll  = 0;
            _reading = true;
            invalidate();
            return true;
        }
        return false;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        if (_reading) {
            ui::header(g, ui::ellipsis(_files[_cursor.sel].name, 20).c_str());
            g.setFont(&fonts::Font0);
            g.setTextColor(TEXT, BG);
            g.setTextDatum(top_left);
            // Font0 at 6px/char gives 39 columns, which is the most text this
            // panel can show while staying readable at arm's length.
            int    drawn = 0, line = 0;
            size_t i = 0;
            while (i < _body.size() && drawn < 8) {
                size_t eol = _body.find('\n', i);
                if (eol == std::string::npos) eol = _body.size();
                for (size_t s = i; s < eol || s == i; s += 39) {
                    if (line++ < _scroll) continue;
                    if (drawn >= 8) break;
                    const size_t n = std::min<size_t>(39, eol - s);
                    g.drawString(_body.substr(s, n).c_str(), PAD,
                                 BODY_Y + 22 + drawn * 11);
                    drawn++;
                    if (n < 39) break;
                }
                i = eol + 1;
            }
            return;
        }

        ui::header(g, "Text Viewer", store::backendName());
        if (_files.empty()) {
            ui::emptyState(g, "No notes yet", "Save one from Notes.");
            return;
        }
        const int rows =
            std::min<int>(ROWS_VISIBLE - 1, static_cast<int>(_files.size()));
        for (int i = 0; i < rows; ++i) {
            const int idx = _cursor.first + i;
            if (idx >= static_cast<int>(_files.size())) break;
            ui::listRow(g, i + 1, idx == _cursor.sel,
                        ui::ellipsis(_files[idx].name, 22).c_str(),
                        ui::humanSize(_files[idx].size).c_str());
        }
        ui::scrollBar(g, static_cast<int>(_files.size()), _cursor.first,
                      ROWS_VISIBLE - 1);
    }

private:
    void scan() {
        _files.clear();
        const char* subs[] = {"notes", "captures", "logs"};
        const char* exts[] = {".txt", ".md"};
        for (const char* sub : subs) {
            for (const char* ext : exts) {
                auto part = store::list(sub, ext, 24);
                _files.insert(_files.end(), part.begin(), part.end());
            }
        }
        _cursor.clamp(static_cast<int>(_files.size()));
    }

    std::vector<store::Entry> _files;
    ListCursor                _cursor;
    std::string               _body;
    int                       _scroll  = 0;
    bool                      _reading = false;
};

// -------------------------------------------------------------- Generator
class GeneratorApp : public App {
public:
    const char* id() const override { return "gen"; }
    const char* title() const override { return "Generator"; }
    const char* hints() const override {
        return "UP/DOWN type  ENTER make";
    }

    void onEnter() override { generate(); }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (e.code == KEY_UP) {
            _kind = (_kind + KINDS - 1) % KINDS;
            generate();
            return true;
        }
        if (e.code == KEY_DOWN) {
            _kind = (_kind + 1) % KINDS;
            generate();
            return true;
        }
        if (e.code == KEY_ENTER) {
            generate();
            return true;
        }
        if (e.code == KEY_S && !_value.empty()) {
            auto items = store::loadSnippets();
            items.emplace_back(kindName(), _value);
            if (store::saveSnippets(items))
                notify::post(Note::Success, "Saved to Snippets");
            else
                notify::post(Note::Error, "Save failed", store::backendName());
            return true;
        }
        return false;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "Generator", kindName());

        g.setFont(&fonts::Font2);
        g.setTextDatum(top_center);
        g.setTextColor(ACCENT, BG);
        // Long passphrases wrap onto a second line rather than being cut.
        if (_value.size() <= 28) {
            g.drawString(_value.c_str(), SCREEN_W / 2, BODY_Y + 40);
        } else {
            g.drawString(_value.substr(0, 28).c_str(), SCREEN_W / 2, BODY_Y + 32);
            g.drawString(_value.substr(28).c_str(), SCREEN_W / 2, BODY_Y + 50);
        }
        g.setTextDatum(top_left);
    }

private:
    static constexpr int KINDS = 3;

    const char* kindName() const {
        switch (_kind) {
            case 0:  return "password";
            case 1:  return "passphrase";
            default: return "number";
        }
    }

    // esp_random() is the hardware RNG, seeded by the radio — genuinely
    // random, unlike rand() on a freshly booted MCU.
    static uint32_t rnd(uint32_t n) { return esp_random() % n; }

    void generate() {
        static const char* CH =
            "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            "!@#$%^&*-_=+";
        static const char* WORDS[] = {
            "amber",   "harbour", "kestrel", "lantern", "meadow", "cobalt",
            "thistle", "quarry",  "willow",  "cinder",  "beacon", "marble",
            "orchard", "pewter",  "ripple",  "saffron", "tundra", "velvet"};

        _value.clear();
        if (_kind == 0) {
            const size_t len = strlen(CH);
            for (int i = 0; i < 20; ++i) _value += CH[rnd(len)];
        } else if (_kind == 1) {
            const int n = sizeof(WORDS) / sizeof(WORDS[0]);
            for (int i = 0; i < 4; ++i) {
                if (i) _value += "-";
                _value += WORDS[rnd(n)];
            }
            char tail[8];
            snprintf(tail, sizeof(tail), "-%02u", (unsigned)rnd(100));
            _value += tail;
        } else {
            char buf[16];
            snprintf(buf, sizeof(buf), "%u", (unsigned)rnd(1000000));
            _value = buf;
        }
        sfx::select();
        invalidate();
    }

    int         _kind = 0;
    std::string _value;
};

}  // namespace

App* makeCalculator() { return new CalculatorApp(); }
App* makeQr() { return new QrApp(); }
App* makeViewer() { return new ViewerApp(); }
App* makeGenerator() { return new GeneratorApp(); }

}  // namespace apps
}  // namespace maz
