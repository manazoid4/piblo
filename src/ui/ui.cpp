#include "ui.h"

#include <time.h>

#include <nod_theme.h>

#include "../core/settings.h"
#include "../core/quiet.h"
#include "../core/sys.h"
#include "../net/mazhost.h"

namespace maz {
namespace ui {

using namespace theme;

// ---------------------------------------------------------------- identity
void mark(M5Canvas& g, int cx, int cy, int r, uint16_t colour, float energy) {
    // Ring + core. `energy` (0..1) swells a second ring: that is the listening
    // animation, reused by Call, Capture and Recorder so "MAZ is hearing you"
    // always looks the same wherever you are.
    if (energy > 0.f) {
        const int er = r + 3 + static_cast<int>(energy * 7.f);
        g.drawCircle(cx, cy, er, LIVE);
        if (energy > 0.55f) g.drawCircle(cx, cy, er + 4, PANEL);
    }
    g.drawCircle(cx, cy, r, colour);
    g.drawCircle(cx, cy, r - 1, colour);
    g.fillCircle(cx, cy, r / 2, colour);
    // The strike through the ring's lower right: the one detail that makes
    // this mark nod and not a generic record dot.
    g.drawLine(cx + r - 1, cy + r - 1, cx + r + 3, cy + r + 3, colour);
}

void wordmark(M5Canvas& g, int cx, int y, uint16_t colour) {
    g.setTextDatum(top_center);
    g.setTextColor(colour, BG);
    g.setFont(&fonts::Font4);
    g.drawString("Piblo", cx, y);
    g.setTextDatum(top_left);
}

// ------------------------------------------------------------------ chrome
static void batteryGlyph(M5Canvas& g, int x, int y, int pct, bool charging) {
    const uint16_t c =
        charging ? TEXT : (pct <= 15 ? ERR : DIM);
    g.drawRect(x, y, 18, 9, c);
    g.fillRect(x + 18, y + 3, 2, 3, c);
    if (pct > 0) g.fillRect(x + 2, y + 2, (14 * pct) / 100, 5, c);
}

void statusBar(M5Canvas& g) {
    g.fillRect(0, 0, SCREEN_W, STATUS_H, PANEL);
    g.drawFastHLine(0, STATUS_H - 1, SCREEN_W, LINE);
    g.setFont(&fonts::Font0);
    g.setTextDatum(top_left);

    // Left: the lamp. Orange only while the mic is live, grey otherwise.
    const uint16_t lamp = Sys.recording ? LIVE : DIM;
    g.fillCircle(6, 7, 3, lamp);
    g.drawCircle(6, 7, 5, lamp);

    g.setTextColor(TEXT, PANEL);
    if (Sys.timeValid) {
        time_t    t = time(nullptr);
        struct tm tmv;
        localtime_r(&t, &tmv);
        char buf[6];
        snprintf(buf, sizeof(buf), "%02d:%02d", tmv.tm_hour, tmv.tm_min);
        g.drawString(buf, 16, 4);
    } else {
        g.setTextColor(DIM, PANEL);
        g.drawString("--:--", 16, 4);
    }

    // Left zone ends before SB_BUDDY_X even with a 1h+ timer: see theme.h.
    int x = SB_UPD_X;
    if (host::updateReady()) {
        g.fillRoundRect(x - 2, 2, 22, 11, 2, TEXT);  // inverse chip = needs you
        g.setTextColor(BG, TEXT);
        g.drawString("UPD", x, 4);
        x = SB_ACT_X;
    }
    if (Sys.recording) {
        g.fillCircle(x + 3, 7, 3, LIVE);
        g.setTextColor(LIVE, PANEL);
        g.drawString(hhmmss(Sys.recSeconds).c_str(), x + 9, 4);
    } else if (Sys.focusRunning) {
        g.setTextColor(TEXT, PANEL);
        g.drawString(hhmmss(Sys.focusRemain).c_str(), x, 4);
    }

    if (Sys.haltKnown && Sys.halted) {
        g.setTextColor(ERR, PANEL);
        g.drawString("HALT", 110, 4);
    } else if (const char* q = quiet::statusText()) {
        g.setTextColor(quiet::level() == quiet::Level::Silent ? WARN : ACCENT2, PANEL);
        g.drawString(q, 110, 4);
    }

    // Claude Code status light (hub /buddy): bright = waiting on you, grey = working.
    if (Sys.buddy) g.fillCircle(SB_BUDDY_X, 7, 3, Sys.buddy == 2 ? TEXT : DIM);

    // Hub dot: white = answering, red = Wi-Fi is up but hub is not, hollow = no Wi-Fi.
    if (Sys.hostOnline) g.fillCircle(SB_CORE_X, 7, 3, TEXT);
    else if (Sys.wifiConnected) g.fillCircle(SB_CORE_X, 7, 3, ERR);
    else g.drawCircle(SB_CORE_X, 7, 3, DIM);

    g.setTextColor(Sys.wifiConnected ? TEXT : DIM, PANEL);
    g.drawString("WiFi", SB_WIFI_X, 4);

    // Battery sits on the right edge with its percentage just inside it.
    batteryGlyph(g, SCREEN_W - 24, 3, Sys.batteryPct < 0 ? 0 : Sys.batteryPct,
                 Sys.charging);
    if (Sys.batteryPct >= 0) {
        char b[6];
        snprintf(b, sizeof(b), "%d%%", Sys.batteryPct);
        g.setTextDatum(top_right);
        g.setTextColor(DIM, PANEL);
        g.drawString(b, SB_PCT_R, 4);
        g.setTextDatum(top_left);
    }
}

void hintBar(M5Canvas& g, const char* hints) {
    const int y = SCREEN_H - HINT_H;
    g.fillRect(0, y, SCREEN_W, HINT_H, PANEL);
    g.drawFastHLine(0, y, SCREEN_W, LINE);
    g.setFont(&fonts::Font0);
    g.setTextDatum(top_left);

    // The back affordance. This device has no pointer, so "back button" has to
    // mean a fixed, always-present label of the key that goes back. Filled
    // amber when there is somewhere to return to, hairline grey on Home when
    // there is not — a control that vanishes is a control you stop trusting.
    const bool canBack = Sys.navDepth > 1;
    g.fillRoundRect(2, y + 1, 32, 11, 2, canBack ? TEXT : LINE);
    g.setTextColor(canBack ? BG : DIM, canBack ? TEXT : LINE);
    g.drawString("<ESC", 6, y + 3);
    // Two or more deep: a tick past the chip says there is still more behind
    // this screen, so you hold ESC for Home rather than mashing it.
    if (Sys.navDepth > 2) g.fillRect(36, y + 1, 2, 11, TEXT);

    // HINT_CHARS is what fits beside the chip at Font0's 6px advance. Hints used
    // to run off the right edge silently; now they are cut where they land.
    // Items are separated by two or more spaces; drop whole trailing items, never cut mid-word.
    std::string h = hints && *hints ? hints : "";
    while (h.size() > static_cast<size_t>(HINT_CHARS)) {
        const size_t cut = h.rfind("  ", h.size() - 1);
        if (cut == std::string::npos || cut == 0) { h.resize(HINT_CHARS); break; }
        h.erase(cut);
        while (!h.empty() && h.back() == ' ') h.pop_back();
    }
    const char* buf = h.c_str();
    g.setTextColor(HINT, PANEL);
    g.drawString(buf, 41, y + 3);
}

void header(M5Canvas& g, const char* title, const char* right) {
    g.setFont(&fonts::Font2);
    g.setTextDatum(top_left);
    // The chip says how to go back; this chevron says there is a back at all,
    // inside the body where the eye already is.
    const int titleX = Sys.navDepth > 1 ? PAD + 10 : PAD;
    if (Sys.navDepth > 1) {
        g.setTextColor(DIM, BG);
        g.drawString("<", PAD, BODY_Y + 2);
    }
    g.setTextColor(TEXT, BG);
    g.drawString(title, titleX, BODY_Y + 2);
    if (right) {
        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, BG);
        g.setTextDatum(top_right);
        g.drawString(right, SCREEN_W - PAD, BODY_Y + 7);
        g.setTextDatum(top_left);
    }
    g.drawFastHLine(PAD, BODY_Y + 19, SCREEN_W - PAD * 2, LINE);
}

// ----------------------------------------------------------------- widgets
void panel(M5Canvas& g, int x, int y, int w, int h, uint16_t fill) {
    g.fillRoundRect(x, y, w, h, 3, fill);
}

void listRow(M5Canvas& g, int visibleIndex, bool selected, const char* label,
             const char* right) {
    const int y = BODY_Y + 1 + visibleIndex * ROW_H;
    if (selected) {
        g.fillRoundRect(2, y, SCREEN_W - 8, ROW_H - 2, 3, TEXT);  // inverse block
    }
    g.setFont(&fonts::Font2);
    g.setTextDatum(top_left);
    g.setTextColor(selected ? BG : TEXT, selected ? TEXT : BG);
    g.drawString(label, 9, y + 1);
    if (right && *right) {
        g.setFont(&fonts::Font0);
        g.setTextDatum(top_right);
        g.setTextColor(selected ? BG : DIM, selected ? TEXT : BG);
        g.drawString(right, SCREEN_W - 12, y + 5);
        g.setTextDatum(top_left);
    }
}

void pillRow(M5Canvas& g, int y, int h, bool selected, const char* label, const char* right) {
    namespace nt = nod::theme;  // same fill/outline as the Talk circle buttons
    const int x = 8, w = SCREEN_W - x - 12;
    g.fillRoundRect(x, y, w, h, 6, nt::BUTTON);
    g.drawRoundRect(x, y, w, h, 6, selected ? nt::TEXT : nt::DIM);
    if (selected) g.drawRoundRect(x + 1, y + 1, w - 2, h - 2, 5, nt::TEXT);
    g.setFont(&fonts::Font2);
    g.setTextDatum(middle_left);
    g.setTextColor(nt::TEXT, nt::BUTTON);
    g.drawString(label, x + 10, y + h / 2);
    if (right && *right) {
        g.setFont(&fonts::Font0);
        g.setTextDatum(middle_right);
        g.setTextColor(nt::DIM, nt::BUTTON);
        g.drawString(right, x + w - 8, y + h / 2);
    }
    g.setTextDatum(top_left);
}

void scrollBar(M5Canvas& g, int total, int firstVisible, int visible) {
    if (total <= visible) return;
    const int trackH = BODY_H - 4;
    const int h      = trackH * visible / total;
    const int y = BODY_Y + 2 + (trackH - h) * firstVisible / (total - visible);
    g.fillRect(SCREEN_W - 4, BODY_Y + 2, 2, trackH, PANEL);
    g.fillRect(SCREEN_W - 4, y, 2, h < 6 ? 6 : h, DIM);
}

void emptyState(M5Canvas& g, const char* line1, const char* line2) {
    g.setTextDatum(middle_center);
    g.setFont(&fonts::Font2);
    g.setTextColor(DIM, BG);
    g.drawString(line1, SCREEN_W / 2, BODY_Y + BODY_H / 2 - (line2 ? 9 : 0));
    if (line2) {
        g.setFont(&fonts::Font0);
        g.drawString(line2, SCREEN_W / 2, BODY_Y + BODY_H / 2 + 12);
    }
    g.setTextDatum(top_left);
}

void bigValue(M5Canvas& g, const char* value, const char* caption,
              uint16_t colour) {
    g.setTextDatum(middle_center);
    g.setFont(&fonts::Font7);
    g.setTextColor(colour, BG);
    g.drawString(value, SCREEN_W / 2, BODY_Y + BODY_H / 2 - 6);
    if (caption) {
        g.setFont(&fonts::Font0);
        g.setTextColor(DIM, BG);
        g.drawString(caption, SCREEN_W / 2, BODY_Y + BODY_H - 10);
    }
    g.setTextDatum(top_left);
}

void progress(M5Canvas& g, int x, int y, int w, int h, float pct,
              uint16_t colour) {
    if (pct < 0) pct = 0;
    if (pct > 1) pct = 1;
    g.fillRoundRect(x, y, w, h, h / 2, PANEL);
    const int fw = static_cast<int>(w * pct);
    if (fw > 1) g.fillRoundRect(x, y, fw, h, h / 2, colour);
}

// -------------------------------------------------------------------- text
StatusWord statusWord(Phase p) {
    using namespace theme;
    static const StatusWord W[] = {
        {"READY", TEXT},     {"LISTENING", LIVE}, {"THINKING", TEXT},  {"SPEAKING", TEXT},
        {"NEEDS YOU", TEXT}, {"OFFLINE", DIM},    {"ERROR", ERR},      {"UPDATE READY", TEXT},
        {"SAVED", OK},
    };
    return W[static_cast<int>(p)];
}

void glyph(M5Canvas& g, int x, int y, int s, Phase p) {
    using namespace theme;
    const uint16_t c = statusWord(p).colour;
    const bool beat = (millis() / 500) & 1;
    const bool anim = p == Phase::Thinking || p == Phase::NeedsYou;
    const int cx = x + s / 2, cy = y + s / 2;
    g.fillRect(x, y, s, s, BG);
    g.drawCircle(cx, cy, s / 2 - 1, p == Phase::Ready ? DIM : c);
    const int dx = s / 5, er = (p == Phase::NeedsYou && beat) ? s / 6 : s / 10;
    const int ey = cy - s / 8 + (p == Phase::Thinking && anim && beat ? s / 8 : 0);
    if (p == Phase::Offline) {
        g.fillRect(cx - dx - er, ey, 2 * er + 1, 2, c);
        g.fillRect(cx + dx - er, ey, 2 * er + 1, 2, c);
    } else {
        g.fillCircle(cx - dx, ey, er > 0 ? er : 1, c);
        g.fillCircle(cx + dx, ey, er > 0 ? er : 1, c);
    }
    if (p == Phase::Listening || p == Phase::Speaking) g.fillCircle(cx, cy + s / 4, s / 10 + 1, c);
    else g.fillRect(cx - s / 6, cy + s / 4, s / 3, 2, c);
}

void stateWord(M5Canvas& g, int x, int y, Phase p, const char* text, bool center) {
    const StatusWord sw = statusWord(p);
    if (!text) text = sw.text;
    const int w = g.textWidth(text);
    const int h = g.fontHeight();
    const bool box = p == Phase::NeedsYou || p == Phase::Update;  // inverse block
    if (center) x -= (w + (box ? 8 : 0)) / 2;
    if (box) {
        g.fillRect(x, y, w + 8, h + 2, TEXT);
        g.setTextColor(BG, TEXT);
        g.setTextDatum(top_left);
        g.drawString(text, x + 4, y + 1);
        return;
    }
    g.setTextColor(sw.colour, BG);
    g.setTextDatum(top_left);
    g.drawString(text, x, y);
    if (p == Phase::Thinking && ((millis() / 500) & 1)) g.fillCircle(x + w + 6, y + h / 2, 3, TEXT);
}

// Word-wrap on spaces/newlines to `cols` columns; a token longer than a line is
// split. One implementation for every screen that shows free text.
std::vector<std::string> wrap(const std::string& text, size_t cols) {
    std::vector<std::string> lines;
    std::string line;
    size_t i = 0;
    while (i <= text.size()) {
        size_t e = text.find_first_of(" \n", i);
        if (e == std::string::npos) e = text.size();
        std::string word = text.substr(i, e - i);
        const bool nl = e < text.size() && text[e] == '\n';
        i = e + 1;
        while (word.size() > cols) {
            if (!line.empty()) { lines.push_back(line); line.clear(); }
            lines.push_back(word.substr(0, cols));
            word.erase(0, cols);
        }
        if (!line.empty() && line.size() + 1 + word.size() > cols) { lines.push_back(line); line.clear(); }
        line += line.empty() ? word : " " + word;
        if (nl) { lines.push_back(line); line.clear(); }
    }
    if (!line.empty()) lines.push_back(line);
    return lines;
}

std::string ellipsis(const std::string& s, size_t maxChars) {
    if (s.size() <= maxChars) return s;
    if (maxChars <= 1) return std::string(maxChars, '.');
    return s.substr(0, maxChars - 1) + "~";
}

std::string hhmmss(uint32_t seconds) {
    char buf[16];
    if (seconds >= 3600)
        snprintf(buf, sizeof(buf), "%u:%02u:%02u", seconds / 3600,
                 (seconds / 60) % 60, seconds % 60);
    else
        snprintf(buf, sizeof(buf), "%02u:%02u", seconds / 60, seconds % 60);
    return buf;
}

std::string humanSize(size_t bytes) {
    char buf[16];
    if (bytes >= 1024 * 1024)
        snprintf(buf, sizeof(buf), "%.1fMB", bytes / (1024.0 * 1024.0));
    else if (bytes >= 1024)
        snprintf(buf, sizeof(buf), "%uKB", (unsigned)(bytes / 1024));
    else
        snprintf(buf, sizeof(buf), "%uB", (unsigned)bytes);
    return buf;
}

std::string stamp(uint32_t epoch) {
    if (epoch == 0) return "--";
    time_t    t = static_cast<time_t>(epoch);
    struct tm tmv;
    localtime_r(&t, &tmv);
    char buf[24];
    snprintf(buf, sizeof(buf), "%04d-%02d-%02d %02d:%02d", tmv.tm_year + 1900,
             tmv.tm_mon + 1, tmv.tm_mday, tmv.tm_hour, tmv.tm_min);
    return buf;
}

}  // namespace ui
}  // namespace maz
