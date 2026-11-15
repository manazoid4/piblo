// Tools, Settings, Connections and Help.
//
// Tools is diagnostics, not a second Bruce: everything here answers "is my
// hardware working" or "what is this device doing", and nothing here touches
// anyone else's network.
#include <esp_system.h>

#include <algorithm>
#include <cstdlib>
#include <vector>

#include "../audio/sfx.h"
#include "../audio/voice.h"
#include "../core/notify.h"
#include "../core/quiet.h"
#include "../core/settings.h"
#include "../core/launcher.h"
#include "../core/shell.h"
#include "../core/sys.h"
#include "../input/keyboard.h"
#include "../net/mazhost.h"
#include "../net/net.h"
#include "../storage/store.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

const char* TOOL_NAMES[] = {"Microphone test", "Speaker test", "Keyboard test",
                            "Wi-Fi scan",      "Battery",      "Memory",
                            "Storage",         "Device info",  "Reboot"};
constexpr int TOOL_COUNT = sizeof(TOOL_NAMES) / sizeof(TOOL_NAMES[0]);

// ------------------------------------------------------------------ Tools
class ToolsApp : public App {
public:
    const char* id() const override { return "tools"; }
    const char* title() const override { return "Tools"; }
    const char* hints() const override {
        return _detail.empty() ? "ENTER run   ESC back" : "ESC back";
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (!_detail.empty()) {
            if ((e.code == KEY_ENTER || e.code == KEY_U) && host::updateReady() &&
                _detail.rfind("nod v", 0) == 0) {
                shell::confirmFwUpdate();
                invalidate();
                return true;
            }
            if (e.code == KEY_ESC) {
                _detail.clear();
                _kbTest = false;
                invalidate();
                return true;
            }
            if (_kbTest) {
                // Keyboard test: echo whatever arrives, including modifiers.
                char line[72];
                snprintf(line, sizeof(line),
                         "code 0x%02X  char '%c'  mods 0x%02X", e.code,
                         e.ch ? e.ch : ' ', e.mods);
                _detail = std::string("Press keys - ESC exits\n") + line;
                invalidate();
                return true;
            }
            return false;
        }
        if (_cursor.onKey(e, TOOL_COUNT)) {
            invalidate();
            return true;
        }
        if (e.code == KEY_ENTER) {
            run(_cursor.sel);
            return true;
        }
        return false;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        if (!_detail.empty()) {
            ui::header(g, TOOL_NAMES[_cursor.sel]);
            g.setFont(&fonts::Font0);
            g.setTextColor(TEXT, BG);
            g.setTextDatum(top_left);
            size_t i   = 0;
            int    row = 0;
            while (i < _detail.size() && row < 8) {
                size_t eol = _detail.find('\n', i);
                if (eol == std::string::npos) eol = _detail.size();
                g.drawString(_detail.substr(i, eol - i).c_str(), PAD,
                             BODY_Y + 22 + row * 11);
                row++;
                i = eol + 1;
            }
            return;
        }

        ui::header(g, "Tools");
        const int rows = std::min(ROWS_VISIBLE - 1, TOOL_COUNT);
        for (int i = 0; i < rows; ++i) {
            const int idx = _cursor.first + i;
            if (idx >= TOOL_COUNT) break;
            ui::listRow(g, i + 1, idx == _cursor.sel, TOOL_NAMES[idx]);
        }
        ui::scrollBar(g, TOOL_COUNT, _cursor.first, ROWS_VISIBLE - 1);
    }

private:
    void run(int idx) {
        char buf[360];
        switch (idx) {
            case 0:
                micTest();
                return;
            case 1: {
                // Same playRaw path as AI replies: if this is silent the speaker
                // or volume is the problem, not the reply download.
                if (!voice::speakerTest(true)) {
                    _detail = "Cannot test while recording";
                } else {
                    snprintf(buf, sizeof(buf),
                             "Played 1s 440Hz tone + sweep.\nVolume %d%% (min 25%% used)\n"
                             "Heard nothing? raise Volume\nor the speaker is dead.",
                             Cfg.volume * 100 / 255);
                    _detail = buf;
                }
                break;
            }
            case 2:
                _kbTest = true;
                _detail = "Press keys - ESC exits";
                break;
            case 3: {
                auto aps = net::scan();
                _detail  = "Nearby networks:\n";
                for (size_t i = 0; i < aps.size() && i < 6; ++i) {
                    snprintf(buf, sizeof(buf), "%-18s %d dBm%s\n",
                             aps[i].ssid.c_str(), (int)aps[i].rssi,
                             aps[i].known ? " *" : "");
                    _detail += buf;
                }
                if (aps.empty()) _detail += "(none found)";
                break;
            }
            case 4:
                snprintf(buf, sizeof(buf),
                         "Level    %d%%\nCharging %s\nVoltage  %d mV",
                         Sys.batteryPct, Sys.charging ? "yes" : "no",
                         (int)M5.Power.getBatteryVoltage());
                _detail = buf;
                break;
            case 5:
                snprintf(buf, sizeof(buf),
                         "Free heap  %u KB\nLargest    %u KB\nSketch     %u KB\n"
                         "Flash      %u MB\nPSRAM      %s",
                         (unsigned)(ESP.getFreeHeap() / 1024),
                         (unsigned)(ESP.getMaxAllocHeap() / 1024),
                         (unsigned)(ESP.getSketchSize() / 1024),
                         (unsigned)(ESP.getFlashChipSize() / (1024 * 1024)),
                         ESP.getPsramSize() ? "yes" : "none (ADV has none)");
                _detail = buf;
                break;
            case 6:
                snprintf(buf, sizeof(buf),
                         "Backend  %s\nSD card  %s\nInternal %s\n"
                         "Free     %s\nTotal    %s",
                         store::backendName(), Sys.sdPresent ? "yes" : "no",
                         Sys.internalFs ? "yes" : "no",
                         ui::humanSize(store::freeBytes()).c_str(),
                         ui::humanSize(store::totalBytes()).c_str());
                _detail = buf;
                break;
            case 7: {
                const std::string cv = host::coreInfo().version;
                const std::string upd = host::updateReady() ? "\nUPDATE READY v" + host::coreInfo().fwVersion : "";
                snprintf(buf, sizeof(buf),
                         "nod v" NOD_FW_VERSION " (" NOD_FW_SHA ")\nCore %s%s\nChip     %s "
                         "rev%d\nCores    %d\nKeyboard %s\nUptime   %s\nBuild    " PIBLO_LABEL,
                         cv.empty() ? "offline" : ("v" + cv).c_str(), upd.c_str(), ESP.getChipModel(),
                         (int)ESP.getChipRevision(), (int)ESP.getChipCores(),
                         KB.ok() ? "TCA8418 ok" : "NOT DETECTED",
                         ui::hhmmss(Sys.uptimeSeconds()).c_str());
                _detail = buf;
                break;
            }
            case 8:
                notify::post(Note::Info, "Rebooting", "back to M5Launcher");
                delay(600);
                if (!launcher::reboot())
                    notify::post(Note::Error, "Launcher", "hand-back failed");
                return;
            default:
                break;
        }
        sfx::confirm();
        invalidate();
    }

    void micTest() {
        // Runs the real capture path for three seconds and reports peak level,
        // so "is the mic dead" has a definite answer rather than a shrug.
        static voice::WavFileSink sink("cache");
        if (!voice::start(&sink, 3)) {
            _detail = std::string("Mic failed to start\n") + voice::lastError();
            invalidate();
            return;
        }
        float          peak = 0.f;
        const uint32_t end  = millis() + 3000;
        while (millis() < end && voice::state() == voice::State::Listening) {
            voice::update();
            peak = std::max(peak, voice::level());
            delay(5);
        }
        voice::stop();
        char buf[180];
        snprintf(buf, sizeof(buf),
                 "Peak level %d%%\n%s\nGain is Settings > Mic gain",
                 (int)(peak * 100),
                 peak < 0.02f ? "No signal - check the mic" : "Microphone works");
        _detail = buf;
        invalidate();
    }

    ListCursor  _cursor;
    std::string _detail;
    bool        _kbTest = false;
};

// ------------------------------------------------------------------ Say
// Type text, ENTER sends it to Core /say and plays the WAV (same download +
// voice::play path as reply speech).
class SayApp : public App {
public:
    const char* id() const override { return "say"; }
    const char* title() const override { return "Say"; }
    const char* hints() const override { return "ENTER speak"; }

    void update() override {
        if (!_sayAt || millis() < _sayAt) return;
        _sayAt = 0;
        if (!_path.empty()) store::remove(_path);
        _path = store::newPath("cache", "wav");
        if (host::say(_text, _path)) voice::play(_path);
        else _path.clear();
        const char* link = linkSentence(true);  // names the real cause: Wi-Fi, pairing, or PC off
        _note = !_path.empty() ? "Playing. ENTER repeats." : link ? link : "Voice unavailable on PC";
        invalidate();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down || e.code == KEY_ESC) return false;
        if (e.code == KEY_ENTER) {
            if (_text.empty() || !store::ready() || _sayAt) return true;
            _note = "Speaking...";
            _sayAt = millis() + 80;  // paint first; host::say blocks for seconds
            invalidate();
            return true;
        } else if (e.code == KEY_BACKSPACE) {
            if (!_text.empty()) _text.pop_back();
            _note.clear();
        } else if (e.ch >= 32 && e.ch < 127 && _text.size() < 300) {
            _text.push_back(e.ch);
            _note.clear();
        } else {
            return false;
        }
        invalidate();
        return true;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "Say");
        g.setFont(&fonts::Font0);
        g.setTextDatum(top_left);
        if (_text.empty()) {
            g.setTextColor(DIM, BG);
            g.drawString("Type what Piblo should say", PAD, BODY_Y + 22);
        } else {
            // Last two lines of 38 chars; the cursor sits on the newest one.
            const size_t n = _text.size();
            const size_t start = n > 76 ? n - 76 : 0;
            const size_t split = start + ((n - start) > 38 ? 38 : 0);
            g.setTextColor(TEXT, BG);
            g.drawString(_text.substr(start, split - start).c_str(), PAD, BODY_Y + 22);
            g.drawString((_text.substr(split) + "_").c_str(), PAD, BODY_Y + 34);
        }
        g.setTextColor(_path.empty() ? ERR : OK, BG);
        g.drawString(_note.c_str(), PAD, BODY_Y + 54);
    }

private:
    std::string _text, _path, _note;
    uint32_t _sayAt = 0;
};

// --------------------------------------------------------------- Settings
const char* SET_NAMES[] = {"Brightness",    "Volume",        "UI sounds",
                           "Screen timeout", "Mic gain",     "Storage",
                           "Time zone",      "Wi-Fi & host", "Keys & help",
                           "Voice",           "Quiet mode", "Quiet for",
                           "Easy mode"};
constexpr int SET_COUNT = sizeof(SET_NAMES) / sizeof(SET_NAMES[0]);

class SettingsApp : public App {
public:
    const char* id() const override { return "settings"; }
    const char* title() const override { return "Settings"; }
    const char* hints() const override {
        const int s = _cursor.sel;
        return s >= 7 && s <= 9 ? "ENTER open" : "</> change  ENTER toggle";
    }

    void onExit() override { Cfg.save(); }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_cursor.onKey(e, SET_COUNT)) {
            invalidate();
            return true;
        }
        const int d = e.code == KEY_RIGHT ? 1 : (e.code == KEY_LEFT ? -1 : 0);
        if (d == 0 && e.code != KEY_ENTER) return false;

        switch (_cursor.sel) {
            case 0:
                Cfg.brightness = clamp8(Cfg.brightness + d * 15);
                Cfg.applyToHardware();
                break;
            case 1:
                Cfg.volume = clamp8(Cfg.volume + d * 15);
                Cfg.applyToHardware();
                break;
            case 2:
                if (e.code == KEY_ENTER) Cfg.uiSounds = !Cfg.uiSounds;
                break;
            case 3: {
                static const uint16_t T[] = {0, 15, 30, 60, 120, 300};
                int                   i   = 0;
                for (int k = 0; k < 6; ++k)
                    if (T[k] == Cfg.screenTimeout) i = k;
                i                 = (i + d + 6) % 6;
                Cfg.screenTimeout = T[i];
                break;
            }
            case 4:
                Cfg.micGain = clamp8(Cfg.micGain + d);
                if (Cfg.micGain < 1) Cfg.micGain = 1;
                voice::begin();  // re-apply gain to the codec
                break;
            case 5:
                if (e.code == KEY_ENTER) {
                    Cfg.preferSd = !Cfg.preferSd;
                    store::remount();
                }
                break;
            case 6:
                Cfg.tzMinutesOffset = static_cast<int8_t>(
                    std::max(-48, std::min(56, Cfg.tzMinutesOffset + d)));
                break;
            case 7:
                if (e.code == KEY_ENTER) shell::pushById("wifi");
                break;
            case 8:
                if (e.code == KEY_ENTER) shell::pushById("help");
                break;
            case 9:
                if (e.code == KEY_ENTER) shell::pushById("voice");
                break;
            case 10: {
                const int lv = (static_cast<int>(quiet::level()) + (d ? d : 1) + 3) % 3;
                quiet::set(static_cast<quiet::Level>(lv), Cfg.quietDur);
                break;
            }
            case 11: {
                const uint8_t dur = static_cast<uint8_t>((Cfg.quietDur + (d ? d : 1) + quiet::DUR_COUNT) % quiet::DUR_COUNT);
                quiet::set(quiet::level(), dur);
                break;
            }
            case 12:
                if (e.code == KEY_ENTER) Cfg.easyMode = !Cfg.easyMode;
                break;
            default:
                break;
        }
        sfx::select();
        invalidate();
        return true;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "Settings", PIBLO_LABEL);

        const int rows = std::min(ROWS_VISIBLE - 1, SET_COUNT);
        for (int i = 0; i < rows; ++i) {
            const int idx = _cursor.first + i;
            if (idx >= SET_COUNT) break;
            ui::listRow(g, i + 1, idx == _cursor.sel, SET_NAMES[idx],
                        value(idx).c_str());
        }
        ui::scrollBar(g, SET_COUNT, _cursor.first, ROWS_VISIBLE - 1);
    }

private:
    static uint8_t clamp8(int v) {
        return static_cast<uint8_t>(v < 0 ? 0 : (v > 255 ? 255 : v));
    }

    std::string value(int idx) const {
        char b[32];
        switch (idx) {
            case 0:
                snprintf(b, sizeof(b), "%d%%", Cfg.brightness * 100 / 255);
                break;
            case 1:
                snprintf(b, sizeof(b), "%d%%", Cfg.volume * 100 / 255);
                break;
            case 2:
                return Cfg.uiSounds ? "on" : "off";
            case 3:
                if (Cfg.screenTimeout == 0) return "never";
                snprintf(b, sizeof(b), "%us", (unsigned)Cfg.screenTimeout);
                break;
            case 4:
                snprintf(b, sizeof(b), "%u", (unsigned)Cfg.micGain);
                break;
            case 5:
                return Cfg.preferSd ? "SD first" : "internal";
            case 6:
                snprintf(b, sizeof(b), "UTC%+d:%02d", Cfg.tzMinutesOffset / 4,
                         abs(Cfg.tzMinutesOffset % 4) * 15);
                break;
            case 7:
                return Sys.wifiConnected ? Sys.wifiSsid : "not connected";
            case 10: {
                const int mins = quiet::remainingMin();
                if (mins < 0) return quiet::levelName(quiet::level());
                snprintf(b, sizeof(b), "%s %dh%02d", quiet::levelName(quiet::level()), mins / 60, mins % 60);
                break;
            }
            case 11:
                return quiet::durLabel(Cfg.quietDur);
            case 12:
                return Cfg.easyMode ? "on" : "off";
            default:
                return "";
        }
        return b;
    }

    ListCursor _cursor;
};

// ------------------------------------------------------------------ Voice
// Pick the reply voice (Fish library, chosen on Core). ENTER saves the choice
// and plays a cached preview through the normal reply playback path.
class VoiceApp : public App {
public:
    const char* id() const override { return "voice"; }
    const char* title() const override { return "Voice"; }
    const char* hints() const override { return "UP/DOWN move  ENTER hear"; }

    void onEnter() override {
        _items.clear();
        _status  = "Loading voices...";
        _pending = Load;
        _at      = millis();
        invalidate();
    }

    void onExit() override {
        if (!_path.empty()) store::remove(_path);
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_cursor.onKey(e, (int)_items.size())) {
            invalidate();
            return true;
        }
        if (e.code == KEY_ENTER && !_items.empty() && _pending == None) {
            _status  = "Switching...";
            _pending = Pick;
            _at      = millis();
            invalidate();
            return true;
        }
        return false;
    }

    void update() override {
        if (_pending == None || millis() - _at < 80) return;  // let the status paint first
        const Pending what = _pending;
        _pending           = None;
        if (what == Load) {
            std::string err;
            if (!host::voiceList(_items, err)) _status = err.empty() ? "no voices" : err;
            else {
                _status.clear();
                for (size_t i = 0; i < _items.size(); ++i)
                    if (_items[i].current) _cursor.sel = (int)i;
                _cursor.clamp((int)_items.size());
            }
        } else {
            host::VoiceItem& v = _items[_cursor.sel];
            if (!host::voiceSelect(v.id, v.name)) {
                _status = "Could not save voice";
            } else {
                for (auto& it : _items) it.current = false;
                v.current = true;
                _status   = "Voice: " + v.name;
                if (store::ready()) {
                    if (!_path.empty()) store::remove(_path);
                    _path = store::newPath("cache", "wav");
                    if (host::voicePreview(v.id, _path)) voice::play(_path);
                    else _status = "Saved; no preview";
                }
            }
        }
        invalidate();
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "Voice", _items.empty() ? nullptr : "Fish");
        if (_items.empty()) {
            ui::emptyState(g, _status.c_str(), "hub must be reachable");
            return;
        }
        const int total = (int)_items.size();
        const int rows  = std::min(ROWS_VISIBLE - 1, total);
        for (int i = 0; i < rows; ++i) {
            const int idx = _cursor.first + i;
            if (idx >= total) break;
            const auto& v = _items[idx];
            std::string label = (v.current ? "* " : "  ") + v.name;
            std::string desc  = v.description.size() > 18 ? v.description.substr(0, 18) : v.description;
            ui::listRow(g, i + 1, idx == _cursor.sel, label.c_str(), desc.c_str());
        }
        ui::scrollBar(g, total, _cursor.first, ROWS_VISIBLE - 1);
        if (!_status.empty()) {
            g.setFont(&fonts::Font0);
            g.setTextDatum(top_left);
            g.setTextColor(DIM, BG);
            g.drawString(_status.c_str(), PAD, BODY_Y + BODY_H - 10);
        }
    }

private:
    enum Pending : uint8_t { None, Load, Pick };
    ListCursor                _cursor;
    std::vector<host::VoiceItem> _items;
    std::string               _status;
    std::string               _path;
    Pending                   _pending = None;
    uint32_t                  _at      = 0;
};

// ------------------------------------------------------------ Connections
class ConnectionsApp : public App {
public:
    const char* id() const override { return "wifi"; }
    const char* title() const override { return "Connections"; }
    const char* hints() const override {
        if (_hostSetup) return "ENTER next/save   ESC cancel";
        if (_entering) return "ENTER connect   ESC cancel";
        if (_scanning) return "ENTER pick network";
        return "W wifi  S scan  C host  H test";
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;

        if (_hostSetup) {
            if (e.code == KEY_ESC) { _hostSetup = 0; _field.text.clear(); invalidate(); return true; }
            if (e.code == KEY_ENTER && !_field.text.empty()) {
                if (_hostSetup == 1) {
                    const size_t colon = _field.text.find(':');
                    Cfg.hostAddr = _field.text.substr(0, colon);
                    if (colon != std::string::npos) Cfg.hostPort = atoi(_field.text.substr(colon + 1).c_str());
                    _field.text.clear(); _hostSetup = 2;
                } else {
                    Cfg.hostToken = _field.text; Cfg.firstRunComplete = true; Cfg.save();
                    _field.text.clear(); _hostSetup = 0;
                    notify::post(Note::Success, "hub saved", Cfg.hostAddr);
                }
                invalidate(); return true;
            }
            if (_field.onKey(e)) { invalidate(); return true; }
            return false;
        }

        if (_entering) {
            if (e.code == KEY_ENTER) {
                if (net::connect(_pending, _field.text)) {
                    Cfg.wifiSsid = _pending;
                    Cfg.wifiPass = _field.text;
                    Cfg.save();
                    notify::post(Note::Success, "Connected", _pending);
                } else {
                    notify::post(Note::Error, "Could not connect", _pending);
                }
                _entering = false;
                _scanning = false;
                invalidate();
                return true;
            }
            if (e.code == KEY_ESC) {
                _entering = false;
                invalidate();
                return true;
            }
            if (_field.onKey(e)) {
                invalidate();
                return true;
            }
            return false;
        }

        if (_scanning) {
            if (_cursor.onKey(e, static_cast<int>(_aps.size()))) {
                invalidate();
                return true;
            }
            if (e.code == KEY_ENTER && !_aps.empty()) {
                _pending = _aps[_cursor.sel].ssid;
                _field.text.clear();
                _entering = true;
                invalidate();
                return true;
            }
            if (e.code == KEY_ESC) {
                _scanning = false;
                invalidate();
                return true;
            }
            return false;
        }

        if (e.code == KEY_W) {
            net::enable(!Sys.wifiOn);
            invalidate();
            return true;
        }
        if (e.code == KEY_S) {
            _aps      = net::scan();
            _scanning = true;
            _cursor.clamp(static_cast<int>(_aps.size()));
            invalidate();
            return true;
        }
        if (e.code == KEY_C) {
            _hostSetup = 1; _field.text.clear(); invalidate(); return true;
        }
        if (e.code == KEY_H) {
            const bool ok = net::probeHost();
            notify::post(ok ? Note::Success : Note::Warn,
                         ok ? "hub reachable" : "hub not reachable",
                         Cfg.hostAddr.empty() ? "not configured" : Cfg.hostAddr);
            invalidate();
            return true;
        }
        if (e.code == KEY_T) {
            const bool ok = net::syncClock();
            notify::post(ok ? Note::Success : Note::Warn, "Clock",
                         ok ? "synced" : "no NTP");
            invalidate();
            return true;
        }
        return false;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);

        if (_hostSetup) {
            ui::header(g, "hub setup", _hostSetup == 1 ? "ADDRESS" : "TOKEN");
            _field.draw(g, PAD, BODY_Y + 30, SCREEN_W - PAD * 2,
                        _hostSetup == 1 ? "192.168.1.20:8787" : "token from host setup");
            return;
        }

        if (_entering) {
            ui::header(g, "Password", _pending.c_str());
            _field.draw(g, PAD, BODY_Y + 30, SCREEN_W - PAD * 2, "password");
            return;
        }
        if (_scanning) {
            ui::header(g, "Networks");
            if (_aps.empty()) {
                ui::emptyState(g, "Nothing found", "press ESC and try again");
                return;
            }
            const int rows =
                std::min<int>(ROWS_VISIBLE - 1, static_cast<int>(_aps.size()));
            for (int i = 0; i < rows; ++i) {
                const int idx = _cursor.first + i;
                if (idx >= static_cast<int>(_aps.size())) break;
                char meta[16];
                snprintf(meta, sizeof(meta), "%d", (int)_aps[idx].rssi);
                ui::listRow(g, i + 1, idx == _cursor.sel,
                            ui::ellipsis(_aps[idx].ssid, 20).c_str(), meta);
            }
            return;
        }

        ui::header(g, "Connections", Sys.wifiOn ? "radio on" : "radio off");
        g.setFont(&fonts::Font0);
        g.setTextDatum(top_left);

        int y = BODY_Y + 24;
        row(g, y, "WI-FI",
            Sys.wifiConnected ? Sys.wifiSsid.c_str() : "not connected",
            Sys.wifiConnected ? OK : DIM);
        y += 16;
        row(g, y, "IP", Sys.ip.empty() ? "-" : Sys.ip.c_str(), DIM);
        y += 16;

        // The MAZ Host line never lies: unconfigured is unconfigured.
        const char* hostState = Cfg.hostAddr.empty()
                                    ? "NOT CONFIGURED"
                                    : (Sys.hostOnline ? "ONLINE" : "OFFLINE");
        row(g, y, "HUB", hostState,
            Sys.hostOnline ? OK : (Cfg.hostAddr.empty() ? DIM : WARN));
        y += 16;
        if (!Cfg.hostAddr.empty()) {
            char addr[56];
            snprintf(addr, sizeof(addr), "%s:%u", Cfg.hostAddr.c_str(),
                     (unsigned)Cfg.hostPort);
            row(g, y, "", addr, DIM);
            y += 16;
        }
        if (!Cfg.hostRemoteUrl.empty()) {
            std::string h = Cfg.hostRemoteUrl;
            if (h.rfind("https://", 0) == 0) h = h.substr(8);
            char rem[56];
            snprintf(rem, sizeof(rem), "Remote: %.44s", h.c_str());
            row(g, y, "", rem, DIM);
        }
    }

private:
    static void row(M5Canvas& g, int y, const char* k, const char* v,
                    uint16_t colour) {
        g.setTextColor(DIM, BG);
        g.drawString(k, PAD, y);
        g.setTextColor(colour, BG);
        g.drawString(v, PAD + 62, y);
    }

    std::vector<net::Ap> _aps;
    ListCursor           _cursor;
    TextField            _field;
    std::string          _pending;
    bool                 _scanning = false;
    bool                 _entering = false;
    uint8_t              _hostSetup = 0;
};

// ------------------------------------------------------------------- Help
const char* HELP_LINES[] = {
    "#EASY MODE",
    "UP/DOWN     choose",
    "ENTER       open",
    "ESC         back",
    "Type as     normal. LEFT/RIGHT",
    "            move the cursor.",
    "Phone       send text to any",
    "            open field and it",
    "            lands here, literal.",
    "Ctrl+P      next saved shortcut",
    "Ctrl+H      next recent phrase",
    "Ctrl+SPACE  talk to fill a field",
    "Settings >  Easy mode turns this",
    "            whole screen off.",
    "#GLOBAL",
    "Ctrl+K      command palette",
    "Ctrl+L      back to M5Launcher",
    "Fn+SPACE    context ask (Talk)",
    "Fn+F        toggle field mode",
    "Fn+Q        quiet / silent / normal",
    "Fn+H        pause agent permission asks",
    "ESC         back (the <ESC chip)",
    "hold ESC    home from anywhere",
    "#HOME",
    "hold SPACE  voice capture",
    "up/down     move a row",
    "left/right  move a column",
    "ENTER       open selected",
    "1-4         quick action",
    "Fn+1-4      cycle quick action",
    "T B N       call pc, capture,",
    "            agent ops",
    "#VOICE",
    "hold SPACE  talk (Call, Capture)",
    "hold ^SPACE call from any screen",
    "P S D       play, save, delete",
    "#LISTS",
    "up/down move   ENTER open",
    "D twice     delete",
    "#ABOUT",
    "Piblo v" NOD_FW_VERSION " on Cardputer ADV",
    "by Maz Works  mazworks.uk",
    "Bruce and Nemo stay available",
    "through M5Launcher.",
};
constexpr int HELP_COUNT = sizeof(HELP_LINES) / sizeof(HELP_LINES[0]);

class HelpApp : public App {
public:
    const char* id() const override { return "help"; }
    const char* title() const override { return "Keys & Help"; }
    const char* hints() const override { return "UP/DOWN scroll"; }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
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
        return false;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        ui::header(g, "Keys & Help", "v" NOD_FW_VERSION);
        g.setFont(&fonts::Font0);
        g.setTextDatum(top_left);

        if (_scroll > HELP_COUNT - 8) _scroll = std::max(0, HELP_COUNT - 8);
        for (int i = 0; i < 8 && _scroll + i < HELP_COUNT; ++i) {
            const char* l = HELP_LINES[_scroll + i];
            g.setTextColor(l[0] == '#' ? ACCENT : TEXT, BG);
            g.drawString(l[0] == '#' ? l + 1 : l, PAD, BODY_Y + 22 + i * 11);
        }
    }

private:
    int _scroll = 0;
};

}  // namespace

App* makeTools() { return new ToolsApp(); }
App* makeSay() { return new SayApp(); }
App* makeSettings() { return new SettingsApp(); }
App* makeVoice() { return new VoiceApp(); }
App* makeConnections() { return new ConnectionsApp(); }
App* makeHelp() { return new HelpApp(); }

}  // namespace apps
}  // namespace maz
