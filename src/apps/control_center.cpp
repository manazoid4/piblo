#include <M5Unified.h>

#include <algorithm>
#include <string>
#include <vector>

#include "../audio/sfx.h"
#include "../core/launcher.h"
#include "../core/notify.h"
#include "../core/settings.h"
#include "../core/shell.h"
#include "../core/sys.h"
#include "../net/mazhost.h"
#include "../net/net.h"
#include "../storage/store.h"
#include "apps.h"
#include "common.h"

namespace maz {
namespace apps {

using namespace theme;

namespace {

struct ControlItem {
    const char* label;
    const char* sub;
    const char* target;
};

constexpr ControlItem CONTROL_ITEMS[] = {
    {"OVERVIEW",    "live state",      nullptr},
    {"WI-FI",       "scan / connect",  "network"},
    {"PC",    "PC / AI bridge",  nullptr},
    {"PC COMMANDS", "media / lock",    "talk"},
    {"LIVE SCREEN", "piblo.local", nullptr},
    {"STORAGE",     "SD / flash",      nullptr},
    {"DIAGNOSTICS", "hardware tests",  "tools"},
    {"SETTINGS",    "device",          "settings"},
    {"M5LAUNCHER",  "firmware library",nullptr},
};
constexpr int CONTROL_COUNT = sizeof(CONTROL_ITEMS) / sizeof(CONTROL_ITEMS[0]);

class ControlCenterApp final : public App {
public:
    const char* id() const override { return "control"; }
    const char* title() const override { return "CONTROL"; }
    const char* hints() const override {
        return _detail ? "ESC list" : "UP/DOWN choose  ENTER open";
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_detail) {
            if (e.code == KEY_ESC) {
                _detail = false;
                invalidate();
                return true;
            }
            return false;
        }
        if (_cursor.onKey(e, CONTROL_COUNT)) {
            sfx::select();
            invalidate();
            return true;
        }
        if (e.code != KEY_ENTER) return false;

        const auto& item = CONTROL_ITEMS[_cursor.sel];
        if (item.target) {
            sfx::confirm();
            shell::pushById(item.target);
            return true;
        }
        if (_cursor.sel == 8) {
            notify::post(Note::Info, "M5Launcher", "opening firmware manager");
            delay(180);
            if (!launcher::reboot())
                notify::post(Note::Error, "M5Launcher", "hand-back failed");
            return true;
        }
        _detail = true;
        sfx::confirm();
        invalidate();
        return true;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        if (_detail) {
            renderDetail(g);
            return;
        }

        std::string top = Sys.wifiConnected ? Sys.wifiSsid :
                          (net::setupApActive() ? "SETUP AP" : "OFFLINE");
        ui::header(g, "CONTROL", top.c_str());

        constexpr int visible = 5;
        if (_cursor.sel < _cursor.first) _cursor.first = _cursor.sel;
        if (_cursor.sel >= _cursor.first + visible)
            _cursor.first = _cursor.sel - visible + 1;
        const int maxFirst = std::max(0, CONTROL_COUNT - visible);
        if (_cursor.first > maxFirst) _cursor.first = maxFirst;

        for (int row = 0; row < visible; ++row) {
            const int idx = _cursor.first + row;
            if (idx >= CONTROL_COUNT) break;
            ui::listRow(g, row + 1, idx == _cursor.sel,
                        CONTROL_ITEMS[idx].label, CONTROL_ITEMS[idx].sub);
        }
        ui::scrollBar(g, CONTROL_COUNT, _cursor.first, visible);
    }

private:
    void renderDetail(M5Canvas& g) {
        const int idx = _cursor.sel;
        if (idx == 0) {
            ui::header(g, "OVERVIEW", "LIVE");
            char line[96];
            const char* wifi = Sys.wifiConnected ? Sys.wifiSsid.c_str() :
                               (net::setupApActive() ? "Piblo-Setup" : "offline");
            snprintf(line, sizeof(line), "Wi-Fi  %s", wifi);
            draw(g, 0, line);
            snprintf(line, sizeof(line), "IP     %s", Sys.ip.empty() ? "-" : Sys.ip.c_str());
            draw(g, 1, line);
            snprintf(line, sizeof(line), "hub     %s", host::linkName());
            draw(g, 2, line);
            snprintf(line, sizeof(line), "Agents %u work / %u wait", Sys.agentsWorking, Sys.agentsWaiting);
            draw(g, 3, line);
            snprintf(line, sizeof(line), "Power  %d%%   SD %s", Sys.batteryPct,
                     Sys.sdPresent ? "ready" : "none");
            draw(g, 4, line);
            return;
        }
        if (idx == 2) {
            ui::header(g, "PC", host::linkName());
            draw(g, 0, "Local hub brain + tools");
            draw(g, 1, "AI: LFM2.5 8B via Ollama");
            draw(g, 2, Sys.hostOnline ? "Host reachable" : "Host offline / not paired");
            std::string addr = Cfg.hostAddr.empty() ? "Host: not configured" :
                               "Host: " + Cfg.hostAddr + ":" + std::to_string(Cfg.hostPort);
            draw(g, 3, addr.c_str());
            draw(g, 4, "Web: Maz Works hub console");
            return;
        }
        if (idx == 4) {
            ui::header(g, "LIVE SCREEN", "2 FPS");
            draw(g, 0, "Open piblo.local");
            draw(g, 1, "Unlock with Piblo token");
            draw(g, 2, "LIVE SCREEN mirrors this LCD");
            draw(g, 3, "240x135 RGB565 / LAN");
            draw(g, 4, "hub can proxy it remotely");
            return;
        }
        if (idx == 5) {
            ui::header(g, "STORAGE", store::backendName());
            std::string free = "Free   " + ui::humanSize(store::freeBytes());
            std::string total = "Total  " + ui::humanSize(store::totalBytes());
            draw(g, 0, Sys.sdPresent ? "SD     present" : "SD     not detected");
            draw(g, 1, Sys.sdUnreadable ? "SD     unreadable" : "SD     filesystem OK");
            draw(g, 2, free.c_str());
            draw(g, 3, total.c_str());
            draw(g, 4, "Firmware .bins live on SD");
            return;
        }
    }

    static void draw(M5Canvas& g, int row, const char* text) {
        g.setFont(&fonts::Font0);
        g.setTextDatum(top_left);
        g.setTextColor(row == 0 ? ACCENT : TEXT, BG);
        g.drawString(text, PAD, BODY_Y + 18 + row * 16);
    }

    ListCursor _cursor;
    bool _detail = false;
};

class NetworkV5App final : public App {
public:
    const char* id() const override { return "network"; }
    const char* title() const override { return "WI-FI"; }
    const char* hints() const override {
        if (_entering) return "type password  ENTER connect  ESC cancel";
        if (_scanning) return "UP/DOWN network  ENTER select  ESC cancel";
        return "UP/DOWN choose  ENTER run";
    }

    void onEnter() override {
        _cursor.sel = 0;
        _cursor.first = 0;
        _menu = true;
        _scanning = false;
        _entering = false;
        _message.clear();
    }

    bool onKey(const KeyEvent& e) override {
        if (!e.down) return false;
        if (_entering) return passwordKey(e);
        if (_scanning) return scanKey(e);

        constexpr int count = 8;
        if (_cursor.onKey(e, count)) {
            sfx::select();
            invalidate();
            return true;
        }
        if (e.code != KEY_ENTER) return false;
        runMenu(_cursor.sel);
        return true;
    }

    void render(M5Canvas& g) override {
        g.fillScreen(BG);
        if (_entering) {
            ui::header(g, _saveSlot == 2 ? "SAVE BACKUP" : "CONNECT", _pending.c_str());
            _field.draw(g, PAD, BODY_Y + 34, SCREEN_W - PAD * 2, "Wi-Fi password");
            label(g, BODY_Y + 17, _saveSlot == 2 ? "Test then save as backup" : "Save as primary on success", DIM);
            if (!_message.empty()) label(g, BODY_Y + 62, _message.c_str(), WARN);
            return;
        }
        if (_scanning) {
            ui::header(g, "WI-FI SCAN", _saveSlot == 2 ? "BACKUP" : "PRIMARY");
            const int visible = 5;
            for (int row = 0; row < visible; ++row) {
                const int idx = _cursor.first + row;
                if (idx >= static_cast<int>(_aps.size())) break;
                char sub[24];
                snprintf(sub, sizeof(sub), "%d%s%s", (int)_aps[idx].rssi,
                         _aps[idx].open ? " open" : "", _aps[idx].known ? " saved" : "");
                ui::listRow(g, row + 1, idx == _cursor.sel, _aps[idx].ssid.c_str(), sub);
            }
            ui::scrollBar(g, static_cast<int>(_aps.size()), _cursor.first, visible);
            return;
        }

        const char* status = Sys.wifiConnected ? "CONNECTED" :
                             (net::setupApActive() ? "SETUP AP" : "OFFLINE");
        ui::header(g, "WI-FI / NETWORK", status);
        constexpr const char* names[] = {
            "STATUS", "RECONNECT NOW", "SCAN PRIMARY", "SCAN BACKUP",
            "DISCONNECT", "FORGET PRIMARY", "FORGET BACKUP", "SETUP HOTSPOT"
        };
        constexpr int count = sizeof(names) / sizeof(names[0]);
        const int visible = 5;
        if (_cursor.sel >= _cursor.first + visible) _cursor.first = _cursor.sel - visible + 1;
        if (_cursor.sel < _cursor.first) _cursor.first = _cursor.sel;
        for (int row = 0; row < visible; ++row) {
            const int idx = _cursor.first + row;
            if (idx >= count) break;
            std::string value;
            if (idx == 0) value = Sys.wifiConnected ? Sys.wifiSsid : (net::setupApActive() ? "192.168.4.1" : "-");
            else if (idx == 5) value = Cfg.wifiSsid.empty() ? "empty" : Cfg.wifiSsid;
            else if (idx == 6) value = Cfg.wifiSsid2.empty() ? "empty" : Cfg.wifiSsid2;
            ui::listRow(g, row + 1, idx == _cursor.sel, names[idx], value.c_str());
        }
        ui::scrollBar(g, count, _cursor.first, visible);
        if (!_message.empty()) label(g, SCREEN_H - HINT_H - 13, _message.c_str(), WARN);
    }

private:
    void runMenu(int idx) {
        _message.clear();
        if (idx == 0) {
            if (Sys.wifiConnected) {
                _message = Sys.ip + " / " + std::to_string(net::rssi()) + " dBm";
            } else if (net::setupApActive()) {
                _message = "Join Piblo-Setup / pass piblosetup";
            } else {
                _message = "Wi-Fi offline";
            }
        } else if (idx == 1) {
            net::disconnect();
            _message = net::connectSaved() ? "Connected: " + Sys.wifiSsid : "Saved networks unavailable";
        } else if (idx == 2 || idx == 3) {
            _saveSlot = idx == 3 ? 2 : 1;
            _aps = net::scan();
            _cursor.sel = _cursor.first = 0;
            _scanning = true;
            if (_aps.empty()) _message = "No networks found";
        } else if (idx == 4) {
            net::disconnect();
            _message = "Disconnected";
        } else if (idx == 5) {
            Cfg.wifiSsid.clear(); Cfg.wifiPass.clear(); Cfg.save();
            _message = "Primary forgotten";
        } else if (idx == 6) {
            Cfg.wifiSsid2.clear(); Cfg.wifiPass2.clear(); Cfg.save();
            _message = "Backup forgotten";
        } else if (idx == 7) {
            _message = net::startSetupAp() ? "Hotspot: Piblo-Setup" : "Could not start hotspot";
        }
        sfx::confirm();
        invalidate();
    }

    bool scanKey(const KeyEvent& e) {
        if (e.code == KEY_ESC) {
            _scanning = false;
            _cursor.sel = _cursor.first = 0;
            invalidate();
            return true;
        }
        if (_cursor.onKey(e, static_cast<int>(_aps.size()))) {
            sfx::select(); invalidate(); return true;
        }
        if (e.code != KEY_ENTER || _aps.empty()) return false;
        _pending = _aps[_cursor.sel].ssid;
        if (_aps[_cursor.sel].open) {
            connectPending("");
            return true;
        }
        _field.text.clear();
        _field.limit = 96;
        _entering = true;
        invalidate();
        return true;
    }

    bool passwordKey(const KeyEvent& e) {
        if (e.code == KEY_ESC) {
            _entering = false;
            _field.text.clear();
            invalidate();
            return true;
        }
        if (e.code == KEY_ENTER) {
            connectPending(_field.text);
            return true;
        }
        if (_field.onKey(e)) { invalidate(); return true; }
        return false;
    }

    void connectPending(const std::string& pass) {
        _message = "Connecting...";
        invalidate();
        const bool ok = net::connect(_pending, pass);
        if (ok) {
            if (_saveSlot == 2) {
                Cfg.wifiSsid2 = _pending;
                Cfg.wifiPass2 = pass;
            } else {
                Cfg.wifiSsid = _pending;
                Cfg.wifiPass = pass;
            }
            Cfg.save();
            _message = (_saveSlot == 2 ? "Backup saved: " : "Connected: ") + _pending;
            notify::post(Note::Success, "Wi-Fi ready", _pending);
        } else {
            _message = "Connection failed - hotspot stays available";
            net::startSetupAp();
        }
        _entering = false;
        _scanning = false;
        _field.text.clear();
        _cursor.sel = _cursor.first = 0;
        invalidate();
    }

    static void label(M5Canvas& g, int y, const char* text, uint16_t color) {
        g.setFont(&fonts::Font0);
        g.setTextDatum(top_left);
        g.setTextColor(color, BG);
        g.drawString(text, PAD, y);
    }

    ListCursor _cursor;
    TextField _field;
    std::vector<net::Ap> _aps;
    std::string _pending;
    std::string _message;
    int _saveSlot = 1;
    bool _menu = true;
    bool _scanning = false;
    bool _entering = false;
};

}  // namespace

App* makeControlCenter() { return new ControlCenterApp(); }
App* makeNetworkV5() { return new NetworkV5App(); }

}  // namespace apps
}  // namespace maz
