// Today: a glance at what your agents did today. Counts only, fetched from Core
// GET /buddy/today. Opening the screen asks once; while it stays open it asks
// again at the same cadence as the Beam / agent-alert poll (15 s, 45 s in FIELD).
#include <string>

#include "../audio/sfx.h"
#include "../core/field.h"
#include "../core/settings.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

class TodayApp : public App {
public:
    const char* id() const override { return "today"; }
    const char* title() const override { return "Today"; }
    const char* hints() const override { return "R refresh"; }

    void onEnter() override { refresh(); }

    bool onKey(const KeyEvent& e) override {
        if (!e.down || e.code != KEY_R) return false;
        sfx::select();
        refresh();
        return true;
    }

    void update() override {
        if (millis() - _askedAt > (Cfg.fieldMode ? 45000u : 15000u)) refresh();
    }

    std::string contextSnapshot() const override {
        const host::TodayStatus& t = field::today();
        if (!t.ok) return "Today unavailable";
        return "Today: " + std::to_string(t.done) + " done, " + std::to_string(t.needs) +
               " need you, " + std::to_string(t.approved) + " approved, " +
               std::to_string(t.denied) + " denied";
    }

    void render(M5Canvas& g) override {
        const host::TodayStatus& t = field::today();
        g.fillScreen(BG);
        ui::header(g, "TODAY", "since midnight");

        if (!t.ok) {
            if (t.error.empty()) ui::emptyState(g, "Loading...", "asking Core");
            else ui::emptyState(g, "Core unreachable", "R retry");
            return;
        }
        if (!t.done && !t.needs && !t.errors && !t.usage && !t.approved && !t.denied && !t.hasLast) {
            ui::emptyState(g, "Nothing yet today", "agents report to Core");
            return;
        }

        const int w = (SCREEN_W - PAD * 2 - 6) / 2;
        const int y = BODY_Y + 22;
        big(g, PAD, y, w, "DONE", t.done, OK);
        big(g, PAD + w + 6, y, w, "NEEDS YOU", t.needs, t.needs ? WARN : DIM);

        g.setFont(&fonts::Font0);
        g.setTextDatum(top_left);
        g.setTextColor(DIM, BG);
        const std::string ad = "APPROVED " + std::to_string(t.approved) + "   DENIED " +
                               std::to_string(t.denied);
        g.drawString(ad.c_str(), PAD, y + 40);

        std::string agents;
        for (int i = 0; i < t.agentCount && i < 3; ++i) {
            if (i) agents += "  ";
            agents += t.agents[i].source + " " + std::to_string(t.agents[i].done);
        }
        g.setTextColor(TEXT, BG);
        g.drawString(ui::ellipsis(agents, 38).c_str(), PAD, y + 53);

        if (t.hasLast) {
            const bool bad = t.lastKind == "error";
            g.setTextColor(bad ? ERR : (t.lastKind == "done" ? OK : WARN), BG);
            std::string last = t.lastSource.empty() ? t.lastText : t.lastSource + ": " + t.lastText;
            g.drawString(ui::ellipsis("> " + last, 38).c_str(), PAD, y + 66);
        }
    }

private:
    static void big(M5Canvas& g, int x, int y, int w, const char* label, int n, uint16_t colour) {
        ui::panel(g, x, y, w, 34);
        g.setTextDatum(top_left);
        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, PANEL);
        g.drawString(label, x + 6, y + 4);
        g.setTextDatum(top_right);
        g.setFont(&fonts::Font4);
        g.setTextColor(colour, PANEL);
        g.drawString(std::to_string(n > 999 ? 999 : n).c_str(), x + w - 6, y + 4);
        g.setTextDatum(top_left);
    }

    void refresh() {
        _askedAt = millis();
        field::requestToday();
        invalidate();
    }

    uint32_t _askedAt = 0;
};

}  // namespace

App* makeToday() { return new TodayApp(); }

}  // namespace apps
}  // namespace maz
