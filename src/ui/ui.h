// Piblo — shared drawing primitives.
// Apps never talk to M5.Display directly; they draw into the shell's canvas
// through these helpers so status bar, hints and dialogs stay consistent.
#pragma once
#include <M5Unified.h>

#include <string>
#include <vector>

#include <talk_home.h>

#include "theme.h"

namespace maz {
namespace ui {

// --- chrome ---------------------------------------------------------------
void statusBar(M5Canvas& g);                   // clock, wifi, rec, battery
void hintBar(M5Canvas& g, const char* hints);  // e.g. "ENTER open   ESC back"
void header(M5Canvas& g, const char* title, const char* right = nullptr);

// --- widgets --------------------------------------------------------------
void panel(M5Canvas& g, int x, int y, int w, int h, uint16_t fill = theme::PANEL);
void listRow(M5Canvas& g, int visibleIndex, bool selected, const char* label,
             const char* right = nullptr);
// Rounded pill row in the Talk-circle button look; y is absolute, right = dim key hint.
void pillRow(M5Canvas& g, int y, int h, bool selected, const char* label, const char* right = nullptr);
void scrollBar(M5Canvas& g, int total, int firstVisible, int visible);
void emptyState(M5Canvas& g, const char* line1, const char* line2 = nullptr);
void bigValue(M5Canvas& g, const char* value, const char* caption,
              uint16_t colour = theme::TEXT);
void progress(M5Canvas& g, int x, int y, int w, int h, float pct, uint16_t colour);

// --- nod identity ---------------------------------------------------------
// The mark: a struck ring with a solid core. Drawn, never bitmapped, so it
// stays crisp at any size and carries no third-party asset licence.
void mark(M5Canvas& g, int cx, int cy, int r, uint16_t colour, float energy = 0.f);
void wordmark(M5Canvas& g, int cx, int y, uint16_t colour);

// --- state words ----------------------------------------------------------
// One word set and one colour per state, shared by Home and Call.
enum class Phase : uint8_t { Ready, Listening, Thinking, Speaking, NeedsYou, Offline, Error, Update, Saved };
struct StatusWord { const char* text; uint16_t colour; };
// Instant press feedback: ENTER lights the footer rule, ESC lights the <ESC chip for ~150 ms.
void pressAck(bool back);
// Talk state -> the shared state word (Home and Call use the same words and colours).
Phase phaseOf(::nod::TalkState s);
StatusWord statusWord(Phase p);
// Small face drawn from primitives: s px square at (x, y). Thinking and
// NeedsYou animate with a 2-frame loop driven by millis().
void glyph(M5Canvas& g, int x, int y, int s, Phase p);
// The state word in its one look: READY plain, LISTENING orange, THINKING with a
// blinking dot, SAVED green, NEEDS YOU / UPDATE an inverse block, ERROR red.
// Uses the current font; `center` treats x as the centre. text=null: stock word.
void stateWord(M5Canvas& g, int x, int y, Phase p, const char* text = nullptr, bool center = false);

// --- text -----------------------------------------------------------------
std::vector<std::string> wrap(const std::string& text, size_t cols);
std::string ellipsis(const std::string& s, size_t maxChars);
std::string hhmmss(uint32_t seconds);
std::string humanSize(size_t bytes);
std::string stamp(uint32_t epoch);  // "2026-08-13 14:05"

}  // namespace ui
}  // namespace maz
