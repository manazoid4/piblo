// Piblo Codes: your 2FA codes, one YES away (docs/protocol/AUTH.md).
//
// The list never holds a secret or a code. ENTER asks Core to type a code on the PC (the normal
// approval appears; YES types it). S asks to show it here: the code arrives only in the response that
// allows it, is drawn huge with a bar that empties, then is wiped. Never stored, never logged.
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "../audio/sfx.h"
#include "../core/approvals.h"
#include "../core/sys.h"
#include "../net/host_worker.h"
#include "../net/mazhost.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

enum class View : uint8_t { Loading, List, Waiting, Reveal };

void wipe(void* p, size_t n) {
    volatile uint8_t* v = static_cast<volatile uint8_t*>(p);
    while (n--) *v++ = 0;
}

class CodesApp final : public App {
public:
    const char* id() const override { return "codes"; }
    const char* title() const override { return "Codes"; }
    const char* hints() const override {
        switch (_view) {
            case View::Reveal:  return "ESC hide";
            case View::Waiting: return "Press YES on the approval";
            default: return _accounts.empty() ? "ESC back" : "ENTER type  S show  ESC back";
        }
    }

    void onEnter() override {
        hide();
        _accounts.clear();
        _cursor = ListCursor();
        _view = View::Loading;
        _error.clear();
        _note.clear();
        _loadAt = millis() + 80;  // paint first; the fetch blocks
        invalidate();
    }

    void onExit() override { hide(); }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_view == View::Reveal || _view == View::Waiting) {
            if (e.code == KEY_ESC) { hide(); _view = View::List; invalidate(); return true; }
            return false;
        }
        if (_view != View::List || _accounts.empty()) return false;
        if (_cursor.onKey(e, static_cast<int>(_accounts.size()))) { sfx::select(); _note.clear(); invalidate(); return true; }
        if (e.code == KEY_ENTER) { queue(Act::Fill); return true; }
        if (e.code == KEY_S || e.code == KEY_RIGHT || e.code == KEY_SLASH) { queue(Act::Show); return true; }
        return false;
    }

    void update() override {
        const uint32_t now = millis();
        if (_view == View::Loading && now >= _loadAt) { load(); return; }
        // Core was off when we opened: retry quietly once it is back.
        if (_view == View::List && _accounts.empty() && !_error.empty() && Sys.hostOnline &&
            !host_worker::busy() && now - _lastTry >= 5000) {
            load();
            return;
        }
        if (_act != Act::None && now >= _actAt) { runAct(); return; }

        if (_view == View::Waiting) {
            host::AuthReveal r;
            if (host::takeAuthReveal(r)) {
                strncpy(_code, r.code, sizeof(_code) - 1);
                _total = r.remaining;
                wipe(&r, sizeof(r));
                _view = View::Reveal;
                _revealEnd = now + static_cast<uint32_t>(_total) * 1000u;
                sfx::confirm();
                invalidate();
            } else if (now - _waitStart > 75000) {
                _view = View::List;
                _note = "Timed out";
                invalidate();
            }
        }
        if (_view == View::Reveal) {
            if (static_cast<int32_t>(now - _revealEnd) >= 0) { hide(); _view = View::List; _note = "Code hidden"; }
            if (now - _lastPaint > 200) { _lastPaint = now; invalidate(); }
        }
    }

    // Only what is on screen, never a code.
    std::string contextSnapshot() const override { return "Codes"; }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "Codes", nullptr);
        switch (_view) {
            case View::Loading: ui::emptyState(g, "Loading..."); break;
            case View::Waiting: renderWaiting(g); break;
            case View::Reveal:  renderReveal(g); break;
            default: renderList(g); break;
        }
    }

private:
    enum class Act : uint8_t { None, Fill, Show };

    void hide() {
        wipe(_code, sizeof(_code));
        _total = 0;
        host::wipeAuthReveal();
    }

    void load() {
        _lastTry = millis();
        const host::AuthCodes r = host::authCodes();
        _view = View::List;
        if (r.ok) {
            _accounts = r.accounts;
            _error.clear();
        } else {
            _accounts.clear();
            _error = r.error == "old_core" ? "Update Piblo on your PC" : (linkSentence(false) ? linkSentence(false) : "Can't reach your PC");
        }
        _cursor.clamp(static_cast<int>(_accounts.size()));
        invalidate();
    }

    void queue(Act a) {
        if (host_worker::busy()) { _note = "Busy, try again"; invalidate(); return; }
        _act = a;
        _actAt = millis() + 60;
        _note = "Asking...";
        invalidate();
    }

    void runAct() {
        const Act a = _act;
        _act = Act::None;
        if (_cursor.sel < 0 || _cursor.sel >= static_cast<int>(_accounts.size())) return;
        std::string err;
        const bool ok = host::authCodeAction(_accounts[_cursor.sel].id, a == Act::Fill ? "fill" : "show", err);
        if (ok) {
            approvals::pollSoon();  // the approval should appear now, not in a few seconds
            if (a == Act::Show) {
                hide();
                _view = View::Waiting;
                _waitStart = millis();
            } else {
                _note = "Press YES to type it";
            }
        } else if (err == "not_found") {
            _note = "Account gone";
            load();
            return;
        } else if (err == "fill_unavailable") {
            _note = "Can't type on this PC";
        } else {
            _note = "Couldn't ask your PC";
        }
        invalidate();
    }

    void renderList(M5Canvas& g) {
        if (_accounts.empty()) {
            if (!_error.empty()) ui::emptyState(g, _error.c_str());
            else ui::emptyState(g, "No codes yet.", "Add them in the Piblo app on your PC.");
            return;
        }
        // Big issuer, small account name. Three rows at 26 px keep it glanceable.
        constexpr int ROW = 26, VIS = 3;
        if (_cursor.sel >= _cursor.first + VIS) _cursor.first = _cursor.sel - VIS + 1;
        if (_cursor.sel < _cursor.first) _cursor.first = _cursor.sel;
        const int n = static_cast<int>(_accounts.size());
        for (int i = 0; i < VIS && _cursor.first + i < n; ++i) {
            const int idx = _cursor.first + i;
            const bool sel = idx == _cursor.sel;
            const int y = BELOW_RULE + 1 + i * ROW;
            if (sel) g.fillRoundRect(2, y, SCREEN_W - 8, ROW - 2, 3, TEXT);
            g.setTextDatum(top_left);
            g.setFont(&fonts::Font2);
            g.setTextColor(sel ? BG : TEXT, sel ? TEXT : BG);
            g.drawString(ui::ellipsis(_accounts[idx].issuer, 24).c_str(), 9, y + 1);
            g.setFont(&fonts::Font0);
            g.setTextColor(sel ? BG : DIM, sel ? TEXT : BG);
            g.drawString(ui::ellipsis(_accounts[idx].label, 36).c_str(), 9, y + 16);
        }
        ui::scrollBar(g, n, _cursor.first, VIS);
        if (!_note.empty()) {
            g.setFont(&fonts::Font0);
            g.setTextColor(ACCENT, BG);
            g.setTextDatum(top_right);
            g.drawString(_note.c_str(), SCREEN_W - 10, BODY_Y + 5);
            g.setTextDatum(top_left);
        }
    }

    void renderWaiting(M5Canvas& g) {
        const std::string issuer = (_cursor.sel >= 0 && _cursor.sel < static_cast<int>(_accounts.size())) ? _accounts[_cursor.sel].issuer : "";
        ui::emptyState(g, ui::ellipsis(issuer + " code", 20).c_str(), "Press YES to see it here");
    }

    void renderReveal(M5Canvas& g) {
        const int32_t leftMs = static_cast<int32_t>(_revealEnd - millis());
        const int left = leftMs > 0 ? (leftMs + 999) / 1000 : 0;
        const size_t len = strlen(_code);
        const size_t half = len / 2;
        char a[8] = {0}, b[8] = {0};
        strncpy(a, _code, half < sizeof(a) - 1 ? half : sizeof(a) - 1);
        strncpy(b, _code + half, sizeof(b) - 1);
        g.setFont(len > 6 ? &fonts::Font6 : &fonts::Font7);
        g.setTextColor(TEXT, BG);
        const int cy = BODY_Y + 52;
        g.setTextDatum(middle_right);
        g.drawString(a, SCREEN_W / 2 - 6, cy);
        g.setTextDatum(middle_left);
        g.drawString(b, SCREEN_W / 2 + 6, cy);
        wipe(a, sizeof(a));
        wipe(b, sizeof(b));
        const float frac = _total > 0 ? static_cast<float>(leftMs > 0 ? leftMs : 0) / (_total * 1000.0f) : 0.f;
        ui::progress(g, PAD + 6, BODY_Y + 86, SCREEN_W - 2 * (PAD + 6) - 30, 8, frac, left > 7 ? OK : left > 3 ? WARN : ERR);
        char t[8];
        snprintf(t, sizeof(t), "%ds", left);
        g.setFont(&fonts::Font2);
        g.setTextColor(DIM, BG);
        g.setTextDatum(middle_right);
        g.drawString(t, SCREEN_W - PAD - 4, BODY_Y + 90);
        g.setTextDatum(top_left);
    }

    View _view = View::Loading;
    Act _act = Act::None;
    std::vector<host::AuthAccount> _accounts;  // capped at AUTH_MAX_ACCOUNTS by the client
    ListCursor _cursor;
    std::string _error, _note;
    char _code[13] = {0};  // only while it is on screen
    int _total = 0;
    uint32_t _loadAt = 0, _actAt = 0, _lastTry = 0, _lastPaint = 0, _waitStart = 0, _revealEnd = 0;
};

}  // namespace

App* makeCodes() { return new CodesApp(); }

}  // namespace apps
}  // namespace maz
