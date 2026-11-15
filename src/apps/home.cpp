#include <array>
#include <cstring>
#include <string>
#include <vector>

#include "../audio/sfx.h"
#include "../core/field.h"
#include "../core/settings.h"
#include "../core/shell.h"
#include "../core/sys.h"
#include "../input/keyboard.h"
#include "../net/mazhost.h"
#include "../net/net.h"
#include <nod_theme.h>
#include <talk_home.h>

#include "../audio/voice.h"
#include "../net/host_worker.h"
#include "../ui/lvgl_ui.h"
#include "../ui/character.h"
#include "../ui/talk_orb.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

// Easy Mode home: plain words, UP/DOWN + ENTER, no Fn. Each row opens a real
// screen; nothing here pretends to work. "Messages" is the results inbox and
// "Shortcuts" is the saved snippets list (see docs/KEYS.md).
struct EasyItem { const char* label; const char* target; };
constexpr EasyItem EASY_ITEMS[] = {
    {"Notes", "notes"},       {"Reminders", "reminders"}, {"Messages", "inbox"},
    {"Tasks", "tasks"},       {"Shortcuts", "snippets"},  {"Settings", "settings"},
    {"Help", "help"},
};
constexpr int EASY_COUNT = sizeof(EASY_ITEMS) / sizeof(EASY_ITEMS[0]);

class HomeApp : public App {
public:
    const char* id() const override { return "home"; }
    const char* title() const override { return "Piblo"; }
    const char* hints() const override {
        if (Cfg.easyMode) {
            const char* link = linkSentence(true);
            return link ? link : "UP/DOWN choose  ENTER open";
        }
        return _hints.c_str();
    }

    void onEnter() override {
        buildPrimary();
        _spaceArmed = false;
        _focus = nod::FOCUS_TALK;
        _easyCursor.clamp(EASY_COUNT);
        lvui::setActive(true);
        rebuildHints();
        invalidate();
    }

    void onExit() override { lvui::setActive(false); }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) {
            if (e.code == KEY_SPACE) _spaceArmed = false;
            return false;
        }

        // Easy Mode: one list, plain old keys. The shell's dispatch already
        // routes every key to the approval overlay first, so nothing here can
        // approve; ESC at Home is a no-op (depth 1 pops to itself).
        if (Cfg.easyMode) {
            if (e.code == KEY_UP || e.code == KEY_DOWN) {
                _easyCursor.onKey(e, EASY_COUNT);
                sfx::select();
                invalidate();
                return true;
            }
            if (e.code == KEY_ENTER) {
                sfx::confirm();
                shell::pushById(EASY_ITEMS[_easyCursor.sel].target);
                return true;
            }
            if (e.code >= KEY_1 && e.code <= KEY_4 && !(e.mods & MOD_FN)) {
                field::runQuick(static_cast<int>(e.code - KEY_1) + 1);
                invalidate();
                return true;
            }
            if (e.code == KEY_U && host::updateReady()) { shell::confirmFwUpdate(); return true; }
            if (e.code == KEY_SPACE) { _spaceArmed = true; return true; }
            return false;
        }

        // ESC: More (grid) goes back to the Talk circle; otherwise stop any reply that is playing.
        if (e.code == KEY_ESC) {
            if (_grid) { _grid = false; sfx::select(); invalidate(); return true; }
            if (voice::isPlaying()) { voice::stopPlayback(); invalidate(); return true; }
            return false;
        }

        if (e.code >= KEY_1 && e.code <= KEY_4) {
            const int slot = static_cast<int>(e.code - KEY_1) + 1;
            if (e.mods & MOD_FN) field::cycleQuick(slot);
            else field::runQuick(slot);
            rebuildHints();
            invalidate();
            return true;
        }

        if (e.code == KEY_U && host::updateReady()) { shell::confirmFwUpdate(); return true; }
        if (e.code == KEY_SPACE) { _spaceArmed = true; return true; }
        if (!_grid) {  // Talk circle Home
            if (e.code == KEY_ENTER) { activate(_focus); return true; }
            const char dir = e.code == KEY_RIGHT ? 'R' : e.code == KEY_LEFT ? 'L' : e.code == KEY_DOWN ? 'D' : e.code == KEY_UP ? 'U' : 0;
            if (dir) { _focus = nod::moveTalkFocus(_focus, dir); sfx::select(); rebuildHints(); invalidate(); return true; }
        } else {
            if (e.code == KEY_ENTER) { open(_sel); return true; }
            if (e.code == KEY_DOWN)  { move(+1); return true; }
            if (e.code == KEY_UP)    { move(-1); return true; }
        }

        size_t n = 0;
        const Descriptor* t = apps::table(n);
        for (size_t i = 0; i < n; ++i) {
            if (t[i].shortcut && t[i].shortcut == e.code) {
                sfx::confirm();
                shell::pushById(t[i].id);
                return true;
            }
        }
        return false;
    }

    void update() override {
        if (_spaceArmed && KB.heldFor(KEY_SPACE) > 250) {
            _spaceArmed = false;
            shell::pushById("braindump");  // keeps recording while SPACE is held
            return;
        }
        rebuildHints();
        // The shell repaints every 250 ms; the Talk circle asks for ~8 fps so the
        // breathing ring and the thinking arc look smooth. Nothing else redraws.
        if (!_grid && millis() - _lastAnim > 120) { _lastAnim = millis(); invalidate(); }
    }

    std::string contextSnapshot() const override {
        if (Cfg.easyMode)
            return std::string("Home selected ") + EASY_ITEMS[_easyCursor.sel].label + "; NOW: " + field::nowText();
        if (!_grid) return std::string("Home: Talk circle; NOW: ") + field::nowText();
        if (_primary.empty() || _sel < 0 || _sel >= static_cast<int>(_primary.size()))
            return field::nowText();
        return std::string("Home selected ") + _primary[_sel]->cellTitle() + "; NOW: " + field::nowText();
    }

    void render(M5Canvas& g) override {
        if (Cfg.easyMode) { renderEasy(g); return; }
        if (!_grid) { renderTalk(g); return; }
        renderMore(g);
    }

private:
    // Same pill list as More (no header row, no footer text over the list); offline shows in the hint bar.
    void renderEasy(M5Canvas& g) {
        g.fillRect(0, BODY_Y, SCREEN_W, BODY_H, nod::theme::BG);
        for (int i = 0; i < MORE_ROWS && _easyCursor.first + i < EASY_COUNT; ++i) {
            const int idx = _easyCursor.first + i;
            ui::pillRow(g, BODY_Y + 1 + i * MORE_PITCH, MORE_PITCH - 2, idx == _easyCursor.sel, EASY_ITEMS[idx].label, "");
        }
        ui::scrollBar(g, EASY_COUNT, _easyCursor.first, MORE_ROWS);
    }

    // More: plain-English names for the registry's home surfaces (the old grid showed CAPS).
    static const char* moreLabel(const Descriptor* d) {
        if (!strcmp(d->id, "talk")) return "Call";
        if (!strcmp(d->id, "desk")) return "Device & PC";
        return d->title;
    }

    // Same pill look as the Talk circle's buttons; one row per surface, scrolls past 5.
    void renderMore(M5Canvas& g) {
        g.fillRect(0, BODY_Y, SCREEN_W, BODY_H, nod::theme::BG);
        const int n = static_cast<int>(_primary.size());
        if (_sel < _first) _first = _sel;
        if (_sel >= _first + MORE_ROWS) _first = _sel - MORE_ROWS + 1;
        for (int i = 0; i < MORE_ROWS && _first + i < n; ++i) {
            const Descriptor* d = _primary[_first + i];
            char key[2] = {d->shortcut ? static_cast<char>('A' + (d->shortcut - KEY_A)) : '\0', '\0'};
            ui::pillRow(g, BODY_Y + 1 + i * MORE_PITCH, MORE_PITCH - 2, _first + i == _sel, moreLabel(d), key);
        }
        ui::scrollBar(g, n, _first, MORE_ROWS);
    }

    void activate(nod::TalkFocus f) {
        sfx::confirm();
        switch (f) {
            case nod::FOCUS_TALK:  shell::pushById("live"); break;  // T still opens push-to-talk Call
            case nod::FOCUS_SAVE:  shell::pushById("braindump"); break;
            case nod::FOCUS_TODAY: shell::pushById("today"); break;
            default:               _grid = true; invalidate(); break;  // More: the full six-surface grid
        }
    }

    // One short line (about 20 chars fit beside the circle): trouble first, then field status.
    std::string shortLine() const {
        static std::string now;
        if (linkSentence(true)) {
            if (!Sys.wifiConnected) return "No Wi-Fi: press W";
            if (!host::configured()) return "Not paired: Control";
            return "Core is off (PC)";
        }
        if (host::updateReady()) return "Update available: press U";
        if (Sys.agentQuestion) return "Agent asked: press N";
        now = field::nowText();
        return now != "READY" ? ui::ellipsis(now, 20) : std::string("Quick: 1-3");
    }

    void renderTalk(M5Canvas& g) {
        namespace nt = nod::theme;
        const nod::TalkHomeLayout L = nod::computeTalkHome({0, BODY_Y, SCREEN_W, BODY_H});
        g.fillRect(0, BODY_Y, SCREEN_W, BODY_H, nt::BG);

        const nod::TalkState st = nod::talkFromSources(
            voice::state() == voice::State::Listening, voice::isPlaying(),
            host_worker::busy() && host_worker::jobKind() == host_worker::JobKind::TalkAudio);
        static const char* const sub[] = {"ENTER", "", "", ""};
        static const char* const word[] = {"Talk", "Listening", "Thinking", "Speaking"};
        // Piblo himself in place of the Talk circle; the word sits under him.
        if (_focus == nod::FOCUS_TALK) g.drawRoundRect(L.cx - L.r, L.cy - L.r, L.r * 2, L.r * 2, 10, nt::TEXT);
        ui::character(g, L.cx - ui::CHAR_W / 2, L.cy - ui::CHAR_H / 2 - 10, st, voice::level(), millis());
        g.setFont(&fonts::Font2);
        g.setTextDatum(middle_center);
        g.setTextColor(nt::TEXT, nt::BG);
        g.drawString(st == nod::TALK_IDLE ? "Talk: ENTER" : word[st], L.cx, L.cy + L.r - 12);
        (void)sub;

        static const char* const hint[3] = {"hold SPACE", "", "all apps"};
        g.setTextDatum(middle_left);
        for (int i = 0; i < 3; ++i) {
            const nod::Rect& b = L.btn[i];
            const bool on = _focus == nod::FOCUS_SAVE + i;
            g.fillRoundRect(b.x, b.y, b.w, b.h, 6, nt::BUTTON);
            g.drawRoundRect(b.x, b.y, b.w, b.h, 6, on ? nt::TEXT : nt::DIM);
            if (on) g.drawRoundRect(b.x + 1, b.y + 1, b.w - 2, b.h - 2, 5, nt::TEXT);
            g.setFont(&fonts::Font2);
            g.setTextColor(nt::TEXT, nt::BUTTON);
            g.drawString(nod::kTalkButton[i], b.x + 10, b.y + b.h / 2);
            if (hint[i][0]) {
                g.setFont(&fonts::Font0);
                g.setTextColor(nt::DIM, nt::BUTTON);
                g.setTextDatum(middle_right);
                g.drawString(hint[i], b.x + b.w - 8, b.y + b.h / 2);
                g.setTextDatum(middle_left);
            }
        }
        g.setFont(&fonts::Font0);
        g.setTextColor(linkSentence(true) ? nt::TEXT : nt::DIM, nt::BG);
        const bool update = !linkSentence(true) && host::updateReady();
        g.drawString(shortLine().c_str(), update ? 4 : L.sentence.x, L.sentence.y + L.sentence.h / 2);
        g.setTextDatum(top_left);
    }

    // Link trouble beats everything else, then a ready update, then what the
    // field layer reports. Same words and colours as Call (ui::statusWord).
    void statusFor(lvui::Status& st) {
        static std::string now;
        const char* link = linkSentence(true);
        ui::Phase ph = ui::Phase::Ready;
        st.sentence = "Hold SPACE: brain dump  T: talk  S: type";
        if (link) {
            ph = ui::Phase::Offline;
            st.sentence = link;
        } else if (host::updateReady()) {
            ph = ui::Phase::Update;
            st.sentence = "Update available: press U";
        } else if (Sys.agentQuestion) {
            ph = ui::Phase::NeedsYou;
            st.sentence = "An agent asked. Press N.";
        }
        const ui::StatusWord w = ui::statusWord(ph);
        st.phase = ph;
        st.line = w.text;
        st.colour = w.colour;
        if (ph == ui::Phase::Ready) {
            now = field::nowText();
            if (now != "READY") st.line = now.c_str();  // outbox, shift, reminder...
        }
    }

    void buildPrimary() {
        _primary.clear();
        size_t n = 0;
        const Descriptor* t = apps::table(n);
        for (size_t i = 0; i < n; ++i)
            if (t[i].onHome && strcmp(t[i].id, "home")) _primary.push_back(&t[i]);
        if (_sel >= static_cast<int>(_primary.size())) _sel = 0;
    }

    void rebuildHints() {
        // W opens Wi-Fi from Home (registry shortcut), so say so when Wi-Fi is down.
        const char* tail = (linkSentence(true) && !Sys.wifiConnected) ? "  W wifi" : host::updateReady() ? "  U update" : "";
        if (!_grid) {
            _hints = std::string(_focus == nod::FOCUS_TALK ? "ENTER talk  hold SPACE save" : "ENTER open") + tail;
            return;
        }
        _hints = std::string("ENTER open") + tail;
    }

    void move(int delta) {
        if (_primary.empty()) return;
        _sel = (_sel + delta + static_cast<int>(_primary.size())) % static_cast<int>(_primary.size());
        sfx::select(); invalidate();
    }

    void open(int slot) {
        if (slot < 0 || slot >= static_cast<int>(_primary.size())) return;
        sfx::confirm(); shell::pushById(_primary[slot]->id);
    }

    std::vector<const Descriptor*> _primary;
    int _sel = 0;
    int _first = 0;
    static constexpr int MORE_ROWS = 5, MORE_PITCH = 21;
    bool _spaceArmed = false;
    bool _grid = false;  // false = Talk circle, true = More (pill list of every surface)
    nod::TalkFocus _focus = nod::FOCUS_TALK;
    ListCursor _easyCursor;
    uint32_t _lastAnim = 0;
    std::string _hints;
};

}  // namespace

const char* linkSentence(bool quiet) {
    if (quiet && (millis() - Sys.bootMillis <= 12000 || Cfg.fieldMode)) return nullptr;
    if (!Sys.wifiConnected) return "No Wi-Fi. Press W to connect.";
    if (!host::configured()) return "Not paired. Control > Pair.";
    if (!Sys.hostOnline) return "Core is off. Start it on PC.";
    return nullptr;
}

App* makeHome() { return new HomeApp(); }

}  // namespace apps
}  // namespace maz
