// Piblo v0.3 — the two product surfaces that replace generic Talk/Nudge
// on Home. Capture keeps its existing proven BrainDump implementation.
#include <algorithm>
#include <array>
#include <string>

#include "../audio/sfx.h"
#include "../audio/voice.h"
#include "../core/notify.h"
#include "../core/settings.h"
#include "../core/shell.h"
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

class AgentsV3App : public App {
public:
    const char* id() const override { return "nudge"; }
    const char* title() const override { return "Agents"; }
    const char* hints() const override {
        return _detail ? "N nudge   ESC list" : "ENTER inspect   N nudge   R refresh";
    }

    void onEnter() override { refresh(); }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (e.code == KEY_R) { refresh(); return true; }
        if (_detail && e.code == KEY_ESC) { _detail = false; invalidate(); return true; }
        if (_cursor.onKey(e, static_cast<int>(_summary.agents.size()))) {
            invalidate();
            return true;
        }
        if (_summary.agents.empty()) {
            if (e.code == KEY_ENTER && !_summary.ok) {
                shell::pushById("talk");
                return true;
            }
            return false;
        }
        if (e.code == KEY_ENTER) { _detail = !_detail; invalidate(); return true; }
        if (e.code == KEY_N) {
            const auto result = host::sendNudge(_summary.agents[_cursor.sel].id);
            notify::post(result.ok ? Note::Success : Note::Error,
                         result.ok ? "Agent nudged" : "Nudge failed",
                         result.ok ? _summary.agents[_cursor.sel].name : result.error);
            refresh();
            return true;
        }
        return false;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "OPS / AGENTS", _summary.ok ? host::linkName() : "OFFLINE");
        g.setFont(&fonts::Font2);
        g.setTextColor(_status == "ALL CLEAR" ? OK : WARN, BG);
        g.drawString(_status.c_str(), PAD, BODY_Y + 18);

        if (_detail && !_summary.agents.empty()) {
            const auto& a = _summary.agents[_cursor.sel];
            g.setFont(&fonts::Font0);
            g.setTextColor(ACCENT, BG);
            g.drawString(ui::ellipsis(a.name, 28).c_str(), PAD, BODY_Y + 43);
            g.setTextColor(TEXT, BG);
            g.drawString((a.provider + " / " + a.sessionState).c_str(), PAD, BODY_Y + 58);
            g.setTextColor(DIM, BG);
            g.drawString(ui::ellipsis(a.evidence.empty() ? "no outstanding evidence" : a.evidence, 36).c_str(), PAD, BODY_Y + 75);
            return;
        }

        const int visible = std::min<int>(4, static_cast<int>(_summary.agents.size()));
        for (int row = 0; row < visible; ++row) {
            const int idx = _cursor.first + row;
            if (idx >= static_cast<int>(_summary.agents.size())) break;
            const auto& a = _summary.agents[idx];
            ui::listRow(g, row + 2, idx == _cursor.sel,
                        ui::ellipsis(a.name, 17).c_str(), a.state.c_str());
        }
        if (_summary.agents.empty())
            ui::emptyState(g, _summary.ok ? "No active agents" : "PC unavailable",
                           _summary.ok ? "Agent Nudge has nothing pending" : "Call hub or check connection");
    }

private:
    void refresh() {
        _summary = host::assurance();
        if (!_summary.ok) _status = Sys.hostOnline ? "NO AGENT DATA" : "PC OFFLINE";
        else if (_summary.questionForMaz) _status = "NEEDS PIBLO";
        else if (_summary.overdue) _status = std::to_string(_summary.overdue) + " OVERDUE";
        else if (_summary.needsNudge) _status = std::to_string(_summary.needsNudge) + " NUDGE DUE";
        else if (_summary.waiting) _status = std::to_string(_summary.waiting) + " WAITING";
        else if (_summary.working) _status = std::to_string(_summary.working) + " WORKING";
        else _status = "ALL CLEAR";
        _cursor.clamp(static_cast<int>(_summary.agents.size()));
        _detail = false;
        invalidate();
    }

    host::Assurance _summary;
    ListCursor _cursor;
    bool _detail = false;
    std::string _status = "PC OFFLINE";
};

}  // namespace

App* makeAgentsV3() { return new AgentsV3App(); }

}  // namespace apps
}  // namespace maz
