// Live: hands-free conversation with Core. The circle shows the state; ESC ends.
#include <nod_theme.h>
#include <talk_home.h>

#include "../audio/live.h"
#include "../core/sys.h"
#include "../ui/character.h"
#include "../ui/talk_orb.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

class LiveApp : public App {
public:
    const char* id() const override { return "live"; }
    const char* title() const override { return "Live"; }
    const char* hints() const override { return "ESC end  any key interrupts"; }

    void onEnter() override { live::begin(); invalidate(); }
    void onExit() override { live::end(); }

    bool onKey(const KeyEvent& e) override {
        if (!e.down || e.code == KEY_ESC || e.code == KEY_GRAVE) return false;  // shell maps ` to ESC and pops
        live::interrupt();
        return true;
    }

    void update() override {
        live::update();
        if (millis() - _anim > 80) { _anim = millis(); invalidate(); }
    }

    void render(M5Canvas& g) override {
        namespace nt = nod::theme;
        const nod::TalkHomeLayout L = nod::computeTalkHome({0, BODY_Y, SCREEN_W, BODY_H});
        g.fillRect(0, BODY_Y, SCREEN_W, BODY_H, nt::BG);
        const live::Phase p = live::phase();
        const nod::TalkState s = p == live::Phase::Listening ? nod::TALK_LISTENING
                               : p == live::Phase::Speaking  ? nod::TALK_SPEAKING
                               : p == live::Phase::Off       ? nod::TALK_IDLE
                                                             : nod::TALK_THINKING;
        const char* word = p == live::Phase::Listening ? "Listening" : p == live::Phase::Thinking ? "Thinking"
                         : p == live::Phase::Speaking ? "Speaking" : p == live::Phase::Connecting ? "Connecting" : "Live";
        ui::character(g, L.cx - ui::CHAR_W / 2, L.cy - ui::CHAR_H / 2 - 10, s, live::level(), millis());
        g.setFont(&fonts::Font2);
        g.setTextDatum(middle_center);
        g.setTextColor(nt::TEXT, nt::BG);
        g.drawString(word, L.cx, L.cy + L.r - 12);
        const char* err = live::error();
        g.setFont(&fonts::Font0);
        g.setTextDatum(middle_left);
        g.setTextColor(err[0] ? nt::TEXT : nt::DIM, nt::BG);
        g.drawString(err[0] ? err : "Hands-free. Just talk.", L.sentence.x, L.sentence.y + L.sentence.h / 2);
        g.setTextDatum(top_left);
    }

private:
    uint32_t _anim = 0;
};

}  // namespace

App* makeLive() { return new LiveApp(); }

}  // namespace apps
}  // namespace maz
