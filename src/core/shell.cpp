#include "shell.h"

#include <algorithm>
#include <cstring>
#include <vector>

#include "../apps/apps.h"
#include "../audio/dictate.h"
#include "../audio/sfx.h"
#include "../audio/voice.h"
#include "../input/keyboard.h"
#include <charge_monitor.h>
#include "../net/host_worker.h"
#include "../net/mazhost.h"
#include "../net/net.h"
#include "../storage/store.h"
#include "../ui/ui.h"
#include "../ui/lvgl_ui.h"
#include "alerts.h"
#include "approvals.h"
#include "field.h"
#include "notify.h"
#include "phone_input.h"
#include "quiet.h"
#include "settings.h"
#include "launcher.h"
#include "sys.h"

namespace maz {
namespace shell {

using namespace theme;

namespace {

M5Canvas          gCanvas(&M5.Display);
std::vector<App*> gStack;
uint32_t          gLastInput   = 0;
bool              gDimmed      = false;
bool              gScreenOff   = false;
uint32_t          gLastPowerMs = 0;
bool              gEscHandled  = false;
bool              gEscClaimed  = false;
bool              gFwConfirm   = false;  // "Update firmware?" modal, any screen

struct FocusTimer {
    bool        running  = false;
    bool        paused   = false;
    uint32_t    total    = 0;
    uint32_t    left     = 0;
    uint32_t    lastTick = 0;
    std::string label;
} gFocus;

void tickFocus() {
    if (!gFocus.running || gFocus.paused) return;
    if (millis() - gFocus.lastTick < 1000) return;
    gFocus.lastTick = millis();
    if (gFocus.left > 0) gFocus.left--;

    Sys.focusRemain = gFocus.left;
    if (gFocus.left == 0) {
        gFocus.running   = false;
        Sys.focusRunning = false;
        sfx::timerDone();
        notify::post(Note::Success, "Focus complete",
                     gFocus.label.empty() ? "session finished" : gFocus.label);
    }
}

void pollPower() {
    const uint32_t cadence = Cfg.fieldMode ? 10000u : 5000u;
    if (millis() - gLastPowerMs < cadence) return;
    gLastPowerMs = millis();

    // M5.Power.isCharging() is unreliable on the Cardputer (worst under
    // M5Launcher). Infer from the voltage trend; a positive M5 reading still wins.
    static nod::ChargeMonitor chargeMon;
    static bool wasCharging = false;
    Sys.batteryPct = M5.Power.getBatteryLevel();
    Sys.batteryMv  = (int)M5.Power.getBatteryVoltage();
    // A computer's USB host is certain power (1). Not plugged into a host is unknown (-1), not
    // "absent": a wall charger has no USB host. M5.Power.isCharging() is no longer trusted: it
    // reported charging while unplugged and kept the device stuck in charging mode.
    chargeMon.feed(millis(), Sys.batteryMv, Serial.isPlugged() ? 1 : -1);
    Sys.charging     = chargeMon.charging();
    Sys.powerState   = (uint8_t)chargeMon.state();
    Sys.chargeEtaMin = chargeMon.etaMinutes();
    if (Sys.charging && !wasCharging && !Sys.recording && !host_worker::busy() &&
        strcmp(currentId(), "home") == 0) {
        pushById("charge");  // plug in at Home -> Charging mode; ESC returns
    }
    wasCharging = Sys.charging;

    if (Sys.batteryPct >= 0 && Sys.batteryPct <= 10 && !Sys.charging &&
        !Sys.lowBatteryWarned) {
        Sys.lowBatteryWarned = true;
        notify::post(Note::Warn, "Battery low", "about to run out");
    }
    if (Sys.batteryPct > 20) Sys.lowBatteryWarned = false;
}

void applyScreenTimeout() {
    uint16_t timeout = Cfg.screenTimeout;
    if (Cfg.fieldMode && (timeout == 0 || timeout > 20)) timeout = 20;
    if (timeout == 0 || Sys.recording || host_worker::busy()) return;
    if (strcmp(currentId(), "charge") == 0) return;  // Charging mode owns the backlight

    const uint32_t idle = (millis() - gLastInput) / 1000;
    if (!gDimmed && idle >= timeout) {
        M5.Display.setBrightness(Cfg.fieldMode ? 8 : 12);
        gDimmed = true;
    }
    if (!gScreenOff && idle >= timeout * 3u) {
        M5.Display.setBrightness(0);
        gScreenOff = true;
    }
}

int score(const char* haystack, const std::string& needle) {
    if (needle.empty()) return 1;
    size_t hi = 0, ni = 0;
    int    hits = 0, streak = 0, best = 0;
    while (haystack[hi] && ni < needle.size()) {
        if (tolower(haystack[hi]) == tolower(needle[ni])) {
            ni++;
            hits++;
            streak++;
            best = std::max(best, streak);
        } else {
            streak = 0;
        }
        hi++;
    }
    if (ni < needle.size()) return 0;
    return hits * 4 + best * 6 - static_cast<int>(hi);
}

class Palette : public App {
public:
    const char* id() const override { return "palette"; }
    const char* title() const override { return "Command"; }
    const char* hints() const override { return "^SPACE say it  ENTER run"; }

    void onEnter() override {
        _query.clear();
        rebuild();
    }

    bool onKey(const KeyEvent& e) override {
        if (e.code == KEY_SPACE && (e.mods & MOD_CTRL)) {
            if (e.down) {
                if (!dictate::active(this)) dictate::start(this);
            } else if (dictate::state() == dictate::State::Listening) {
                dictate::stop();
            }
            return true;
        }
        if (!e.down) return false;
        if (dictate::active(this) && e.code == KEY_ESC) {
            dictate::cancel();
            return true;
        }
        if (e.code == KEY_BACKSPACE) {
            if (!_query.empty()) _query.pop_back();
            rebuild();
            return true;
        }
        if (e.code == KEY_UP) {
            if (_sel > 0) _sel--;
            invalidate();
            return true;
        }
        if (e.code == KEY_DOWN) {
            if (_sel + 1 < static_cast<int>(_hits.size())) _sel++;
            invalidate();
            return true;
        }
        if (e.code == KEY_ENTER) {
            if (_hits.empty()) return true;
            const char* target = _hits[_sel]->id;
            pop();
            pushById(target);
            sfx::confirm();
            return true;
        }
        if (e.ch >= 32 && e.ch < 127) {
            _query.push_back(e.ch);
            rebuild();
            return true;
        }
        return false;
    }

    std::string contextSnapshot() const override {
        return std::string("Command palette query: ") + _query;
    }

    void render(M5Canvas& g) override {
        std::string spoken;
        if (dictate::take(this, spoken)) {
            _query += spoken;
            rebuild();
        }

        g.fillScreen(BG);
        ui::header(g, "Command", store::backendName());
        ui::panel(g, PAD, BODY_Y + 22, SCREEN_W - PAD * 2, 18);
        g.setFont(&fonts::Font2);
        g.setTextDatum(top_left);

        if (dictate::active(this)) {
            const bool listening = dictate::state() == dictate::State::Listening;
            g.setTextColor(listening ? LIVE : TEXT, PANEL);
            g.drawString(listening ? "listening..." : "transcribing...",
                         PAD + 4, BODY_Y + 23);
            return;
        }

        g.setTextColor(TEXT, PANEL);
        std::string shown = "> " + _query;
        if ((millis() / 500) % 2) shown += "_";
        g.drawString(shown.c_str(), PAD + 4, BODY_Y + 23);

        if (_hits.empty()) {
            g.setTextColor(DIM, BG);
            g.setFont(&fonts::Font0);
            g.drawString("no match", PAD + 4, BODY_Y + 48);
            return;
        }
        const int rows = std::min<int>(3, static_cast<int>(_hits.size()));
        const int from = std::max(0, _sel - 2);
        for (int i = 0; i < rows; ++i) {
            const int idx = from + i;
            if (idx >= static_cast<int>(_hits.size())) break;
            const int y = BODY_Y + 44 + i * 17;
            if (idx == _sel)
                g.fillRoundRect(PAD, y - 1, SCREEN_W - PAD * 2, 16, 3, ACCENT);
            g.setFont(&fonts::Font2);
            g.setTextColor(idx == _sel ? BG : TEXT, idx == _sel ? ACCENT : BG);
            g.drawString(_hits[idx]->title, PAD + 6, y);
        }
    }

private:
    void rebuild() {
        _hits.clear();
        size_t n = 0;
        const apps::Descriptor* t = apps::table(n);
        std::vector<std::pair<int, const apps::Descriptor*>> ranked;
        for (size_t i = 0; i < n; ++i) {
            if (!strcmp(t[i].id, "home")) continue;
            const int s = std::max(score(t[i].title, _query), score(t[i].keywords, _query));
            if (s > 0) ranked.emplace_back(s, &t[i]);
        }
        std::sort(ranked.begin(), ranked.end(),
                  [](const std::pair<int, const apps::Descriptor*>& a,
                     const std::pair<int, const apps::Descriptor*>& b) {
                      return a.first > b.first;
                  });
        for (auto& r : ranked) _hits.push_back(r.second);
        _sel = 0;
        invalidate();
    }

    std::string _query;
    std::vector<const apps::Descriptor*> _hits;
    int _sel = 0;
};

void bootScreen() {
    M5.Display.fillScreen(BG);
    const uint32_t t0 = millis();
    while (millis() - t0 < T_BOOT) {
        const float p = (millis() - t0) / static_cast<float>(T_BOOT);
        gCanvas.fillScreen(BG);
        ui::mark(gCanvas, SCREEN_W / 2, 44, 12, ACCENT, p < 0.7f ? p : 0.f);
        if (p > 0.35f) ui::wordmark(gCanvas, SCREEN_W / 2, 62, TEXT);
        if (p > 0.8f) {
            gCanvas.setFont(&fonts::Font0);
            gCanvas.setTextDatum(top_center);
            gCanvas.setTextColor(DIM, BG);
            gCanvas.drawString("v" NOD_FW_VERSION, SCREEN_W / 2, 118);
            gCanvas.setTextDatum(top_left);
        }
        gCanvas.pushSprite(0, 0);
        delay(16);
    }
}

uint8_t navFallback(uint8_t code) {
    switch (code) {
        case KEY_SEMICOLON:  return KEY_UP;
        case KEY_COMMA:      return KEY_LEFT;
        case KEY_DOT:        return KEY_DOWN;
        case KEY_SLASH:      return KEY_RIGHT;
        case KEY_GRAVE:      return KEY_ESC;
        default:             return KEY_NONE;
    }
}

bool handleGlobalKey(const KeyEvent& e) {
    if (!e.down) return false;
    if (gFwConfirm) {  // modal: one key yes, ESC no, everything else swallowed
        if (e.physical && (e.code == KEY_U || e.code == KEY_Y)) {
            gFwConfirm = false;
            host::fwUpdate();  // reboots on success; shows its own error otherwise
            invalidate();
        } else if (e.code == KEY_ESC || e.code == KEY_N) {
            gFwConfirm = false;
            invalidate();
        }
        return true;
    }
    // Ctrl+U: install the available firmware update from any screen (Ctrl never types text).
    if ((e.mods & MOD_CTRL) && e.code == KEY_U) {
        confirmFwUpdate();
        return true;
    }
    if ((e.mods & MOD_CTRL) && e.code == KEY_K) {
        openPalette();
        return true;
    }
    // Call from any screen: hold Ctrl+Space (Call starts recording while SPACE is held).
    if ((e.mods & MOD_CTRL) && e.code == KEY_SPACE && !gStack.empty() &&
        strcmp(gStack.back()->id(), "talk") && strcmp(gStack.back()->id(), "palette")) {
        field::clearContext();
        pushById("talk");
        return true;
    }
    if ((e.mods & MOD_CTRL) && e.code == KEY_L) {
        launcher::reboot();
        return true;
    }
    if ((e.mods & MOD_FN) && e.code == KEY_SPACE && !gStack.empty()) {
        field::armContext(gStack.back()->id(), gStack.back()->contextSnapshot());
        if (strcmp(gStack.back()->id(), "talk")) pushById("talk");
        notify::post(Note::Info, "Context Ask", field::context().c_str());
        return true;
    }
    if ((e.mods & MOD_FN) && e.code == KEY_H) {
        field::toggleHalt();
        return true;
    }
    if ((e.mods & MOD_FN) && e.code == KEY_Q) {
        quiet::cycle();
        return true;
    }
    if ((e.mods & MOD_FN) && e.code == KEY_F) {
        field::toggleFieldMode();
        return true;
    }
    return false;
}

}  // namespace

M5Canvas& canvas() { return gCanvas; }
int depth() { return static_cast<int>(gStack.size()); }
const char* currentId() { return gStack.empty() ? "none" : gStack.back()->id(); }

void confirmFwUpdate() {
    if (host::updateReady()) {
        gFwConfirm = true;
        invalidate();
    } else {
        notify::post(Note::Info, "No update", "firmware is current");
    }
}

void invalidate() {
    if (!gStack.empty()) gStack.back()->invalidate();
}

void wake() {
    gLastInput = millis();
    if (gDimmed || gScreenOff) {
        Cfg.applyToHardware();
        gDimmed = false;
        gScreenOff = false;
        invalidate();
    }
}

void push(App* app) {
    if (!app) return;
    if (!gStack.empty()) gStack.back()->onExit();
    gStack.push_back(app);
    Sys.navDepth = static_cast<uint8_t>(gStack.size());
    app->onEnter();
    app->invalidate();
}

bool pushById(const char* id) {
    App* a = apps::create(id);
    if (!a) {
        notify::post(Note::Error, "Unknown command", id);
        return false;
    }
    push(a);
    return true;
}

void pop() {
    if (gStack.size() <= 1) return;
    gStack.back()->onExit();
    delete gStack.back();
    gStack.pop_back();
    Sys.navDepth = static_cast<uint8_t>(gStack.size());
    gStack.back()->onEnter();
    gStack.back()->invalidate();
}

void goHome() {
    while (gStack.size() > 1) pop();
}

void openPalette() {
    if (!gStack.empty() && !strcmp(gStack.back()->id(), "palette")) return;
    push(new Palette());
}

void dispatchKey(const KeyEvent& e) {
    const bool wasAsleep = gDimmed || gScreenOff;
    if (e.down) wake();
    // Approvals sit above everything. A key that only woke the screen never decides.
    if (approvals::active()) {
        if (!wasAsleep) approvals::handleKey(e);
        return;
    }
    if (alerts::active()) {
        if (wasAsleep) return;                 // that key only woke the screen
        if (alerts::handleKey(e)) return;      // A/Enter/S/Esc only; Fn and other keys fall through
    }
    if (notify::active() && e.down) notify::dismiss();

    if (handleGlobalKey(e)) return;
    if (gStack.empty()) return;

    KeyEvent ev = e;
    if (ev.down && ev.code == KEY_ESC) {
        gEscHandled = false;
        gEscClaimed = false;
    }
    if (gStack.back()->onKey(ev)) {
        if (ev.down && ev.code == KEY_ESC) gEscClaimed = true;
        return;
    }

    const uint8_t nav = navFallback(ev.code);
    if (nav != KEY_NONE) {
        ev.code = nav;
        ev.ch = 0;
        if (ev.down && nav == KEY_ESC) {
            gEscHandled = false;
            gEscClaimed = false;
        }
        if (gStack.back()->onKey(ev)) {
            if (ev.down && nav == KEY_ESC) gEscClaimed = true;
            return;
        }
    }

    if (!ev.down && ev.code == KEY_ESC && !gEscHandled && !gEscClaimed) {
        pop();
        sfx::select();
    }
}

namespace focus {
void start(uint32_t seconds, const std::string& lbl) {
    gFocus.running = true;
    gFocus.paused = false;
    gFocus.total = seconds;
    gFocus.left = seconds;
    gFocus.label = lbl;
    gFocus.lastTick = millis();
    Sys.focusRunning = true;
    Sys.focusRemain = seconds;
    Sys.focusLabel = lbl;
}
void pause() { gFocus.paused = true; }
void resume() { gFocus.paused = false; gFocus.lastTick = millis(); }
void cancel() {
    gFocus.running = false;
    gFocus.paused = false;
    gFocus.left = 0;
    Sys.focusRunning = false;
    Sys.focusRemain = 0;
}
bool running() { return gFocus.running; }
bool paused() { return gFocus.paused; }
uint32_t remaining() { return gFocus.left; }
uint32_t total() { return gFocus.total; }
const std::string& label() { return gFocus.label; }
}  // namespace focus

bool begin() {
    Serial.printf("[boot] shell canvas heap=%u\n",
                  static_cast<unsigned>(ESP.getFreeHeap()));
    gCanvas.setColorDepth(16);
    if (!gCanvas.createSprite(SCREEN_W, SCREEN_H)) {
        ESP_LOGE("shell", "canvas allocation failed");
        return false;
    }
    Serial.printf("[boot] canvas ready heap=%u\n",
                  static_cast<unsigned>(ESP.getFreeHeap()));
    bootScreen();
    Serial.println("[boot] lvgl begin");
    if (!lvui::begin(gCanvas)) {
        ESP_LOGE("shell", "LVGL display allocation failed");
        return false;
    }
    Serial.printf("[boot] lvgl ready heap=%u\n",
                  static_cast<unsigned>(ESP.getFreeHeap()));
    push(apps::makeHome());
    gLastInput = millis();
    sfx::boot();
    return true;
}

void loop() {
    M5.update();
    KB.update();

    KeyEvent e;
    bool sawInput = false;
    while (KB.pop(e)) {
        sawInput = true;
        dispatchKey(e);
    }

    if (KB.held(KEY_ESC) && KB.heldFor(KEY_ESC) > 600 && !gEscHandled && !gEscClaimed) {
        gEscHandled = true;
        goHome();
        sfx::select();
    }
    if (sawInput) wake();

    tickFocus();
    pollPower();
    voice::update();
    dictate::update();
    net::update();
    field::update();
    if (approvals::update(gScreenOff)) wake();
    alerts::update();
    phone::update();  // phone-keyboard acks piggyback the host worker
    notify::update();
    quiet::update();
    apps::updateProductServices();
    lvui::tick();
    applyScreenTimeout();

    if (gStack.empty()) return;
    App* top = gStack.back();
    top->update();

    static uint32_t lastPaint = 0;
    const bool due = millis() - lastPaint > (approvals::holding() ? 50u : 250u);
    if (!top->dirty() && !due) return;
    if (gScreenOff) return;

    top->render(gCanvas);
    ui::statusBar(gCanvas);
    ui::hintBar(gCanvas, top->hints());
    notify::render(gCanvas);
    alerts::render(gCanvas);
    approvals::render(gCanvas);
    if (gFwConfirm) {
        gCanvas.fillRoundRect(20, 38, SCREEN_W - 40, 52, 4, PANEL);
        gCanvas.drawRoundRect(20, 38, SCREEN_W - 40, 52, 4, ACCENT);
        gCanvas.setFont(&fonts::Font0);
        gCanvas.setTextDatum(top_center);
        gCanvas.setTextColor(TEXT, PANEL);
        gCanvas.drawString("Update firmware?", SCREEN_W / 2, 46);
        gCanvas.setTextColor(DIM, PANEL);
        gCanvas.drawString(("-> v" + host::coreInfo().fwVersion).c_str(), SCREEN_W / 2, 60);
        gCanvas.setTextColor(ACCENT, PANEL);
        gCanvas.drawString("U/Y on device   ESC no", SCREEN_W / 2, 74);
        gCanvas.setTextDatum(top_left);
    }
    // Idle repaints (clock tick, nothing changed) re-sent 64.8 KB over SPI every 250 ms (~13 ms
    // blocked). Skip the push when the frame is byte-identical; refresh at least 1/s in case
    // something drew straight to M5.Display (fwFail text, wake).
    static uint32_t lastHash = 0, lastPush = 0;
    const uint32_t* px = static_cast<const uint32_t*>(gCanvas.getBuffer());
    uint32_t h = 2166136261u;
    for (size_t i = 0, n = SCREEN_W * SCREEN_H / 2; i < n; ++i) h = (h ^ px[i]) * 16777619u;
    if (h != lastHash || millis() - lastPush > 1000) {
        gCanvas.pushSprite(0, 0);
        lastHash = h;
        lastPush = millis();
    }
    top->clean();
    lastPaint = millis();
}

}  // namespace shell
}  // namespace maz
