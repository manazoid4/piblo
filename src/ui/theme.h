// Piblo — the single source of truth for how the device looks.
#pragma once
#include <stdint.h>

namespace maz {
namespace theme {

constexpr int SCREEN_W = 240;
constexpr int SCREEN_H = 135;

constexpr int STATUS_H = 15;
constexpr int HINT_H   = 14;
constexpr int BODY_Y   = STATUS_H;
constexpr int BELOW_RULE = BODY_Y + 22;  // first y under ui::header's rule (BODY_Y+19)
constexpr int BODY_H   = SCREEN_H - STATUS_H - HINT_H;
constexpr int PAD      = 6;
constexpr int ROW_H    = 17;
constexpr int ROWS_VISIBLE = BODY_H / ROW_H;
constexpr int LIST_ROWS = ROWS_VISIBLE - 1;

// Home shows at most four actions (TABLE_PAGE): CALL, CAPTURE, AGENTS,
// CONTROL. Everything else lives in Ctrl+K instead of competing on Home.
constexpr int HOME_STATUS_H = 36;  // big status line + one small line
constexpr int TABLE_Y      = BODY_Y + HOME_STATUS_H + 2;
constexpr int TABLE_COLS   = 2;
constexpr int TABLE_ROWS   = 2;
constexpr int TABLE_CELL_W = SCREEN_W / TABLE_COLS;
constexpr int TABLE_CELL_H = 30;
constexpr int TABLE_PAGE   = TABLE_COLS * TABLE_ROWS;

// RGB565 — Concept A "Toy-shop warm" (single source of truth: design/tokens-a.css).
// Warm paper+ink base with the three brand accents: tomato = talk/live, mint =
// saved/ok, sun = selection/emphasis. PANEL, DIM and ERR are lighten/darken
// derivations of the token colours so the 240x135 screen keeps a readable ramp.
constexpr uint16_t BG      = 0x28C2;  // ink #2A1A12 (screen-bg)
constexpr uint16_t PANEL   = 0x3943;  // derived #3B281A (between ink and ink-soft)
constexpr uint16_t LINE    = 0x5A06;  // ink-soft #5B4336
constexpr uint16_t TEXT    = 0xFFBC;  // cream #FFF4E0 (screen-ink)
constexpr uint16_t DIM     = 0xBCCD;  // derived muted tan #B89A6E
constexpr uint16_t HINT    = 0xFF17;  // paper-2 #FBE3BD
constexpr uint16_t LIVE    = 0xEAC6;  // tomato #EE5A36 (screen-talk) — mic is live
constexpr uint16_t ACCENT  = 0xFE47;  // sun #FFC93C
constexpr uint16_t ACCENT2 = TEXT;    // cream
constexpr uint16_t OK      = 0x8EF7;  // mint #8FDCB8 (screen-ring) — saved
constexpr uint16_t WARN    = 0xFE47;  // sun #FFC93C
constexpr uint16_t ERR     = 0xE245;  // derived bright warm red #E04A2E (tomato-deep lightened)
// The talk dot stays blue inside the warm palette (Maz, 7 Nov).
constexpr uint16_t TALK_BLUE     = 0xEC07;  // orange cheeks #E8833A
constexpr uint16_t TALK_BLUE_HI  = 0xF739;  // cream #F3E6C8
constexpr uint16_t TALK_BLUE_DIM = 0x5BC7;  // olive #5B7A3A

namespace rgb {
constexpr uint32_t BG     = 0x2A1A12;
constexpr uint32_t PANEL  = 0x3B281A;
constexpr uint32_t LINE   = 0x5B4336;
constexpr uint32_t TEXT   = 0xFFF4E0;
constexpr uint32_t DIM    = 0xB89A6E;
constexpr uint32_t LIVE   = 0xEE5A36;
constexpr uint32_t ACCENT = 0xFFC93C;
constexpr uint32_t ACCENT2 = 0xFFF4E0;
constexpr uint32_t OK     = 0x8FDCB8;
constexpr uint32_t WARN   = 0xFFC93C;
constexpr uint32_t ERR    = 0xE04A2E;
constexpr uint32_t TALK_BLUE     = 0xE8833A;
constexpr uint32_t TALK_BLUE_HI  = 0xF3E6C8;
constexpr uint32_t TALK_BLUE_DIM = 0x5B7A3A;
}  // namespace rgb

// Calm UI tokens (see docs/UI-GUIDE.md). Fonts: state word Font4 scaled,
// sub lines Font2, body and chrome Font0 (6x8 px per character).
constexpr int   HINT_CHARS        = 33;    // fits beside the <ESC chip
constexpr int   TOAST_TITLE_CHARS = 31;    // Font2 inside the toast box
constexpr int   TOAST_TEXT_CHARS  = 36;    // Font0 detail line
constexpr float STATE_SCALE       = 1.5f;  // Font4 multiplier for state words
constexpr int   STATE_Y           = BODY_Y + 2;
constexpr int   REASON_Y          = BODY_Y + 42;
constexpr int   REPLY_Y           = BODY_Y + 60;
constexpr int   REPLY_LINE_H      = 11;
constexpr int   REPLY_LINES       = 4;
constexpr int   REPLY_CHARS       = 38;
// Status bar columns, left to right, so nothing overlaps at 240 px.
constexpr int   SB_UPD_X   = 50;   // "UPD" badge, 18 px
constexpr int   SB_ACT_X   = 74;   // recording / focus timer, up to 57 px
constexpr int   SB_BUDDY_X = 142;  // Claude Code light
constexpr int   SB_CORE_X  = 152;  // Core dot
constexpr int   SB_WIFI_X  = 160;  // "WiFi", 24 px
constexpr int   SB_PCT_R   = 213;  // battery % right edge, up to 24 px

constexpr uint32_t T_FAST  = 120;
// A boot logo should brand the device, not hold it hostage. Hardware and LVGL
// init still happen normally; this only removes 650 ms of intentional waiting.
constexpr uint32_t T_BOOT  = 250;
constexpr uint32_t T_TOAST = 1800;

}  // namespace theme
}  // namespace maz
