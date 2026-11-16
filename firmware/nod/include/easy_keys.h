// Piblo - Easy Mode key contract: the one place that says which keys do
// what, in both modes, so the firmware, the help screen and docs/KEYS.md
// cannot drift apart.
//
// Rules pinned here (native tests in tests/test_nod.cpp enforce them):
//   * One key table everywhere (owner, 16 Nov): ENTER = yes/do, DEL = no/deny,
//     ESC = back (never denies), hold SPACE = speak, L = later.
//   * Only Y and ENTER can approve. No Easy Mode shortcut, remap,
//     auto-repeat or "any key" path may approve, and phone text never can.
//     A timeout or lost connection is a rejection (approvals.cpp owns that;
//     this header only names the two explicit approve keys).
//   * Outside text entry, ESC/BACKSPACE go back. Inside text entry, BACKSPACE
//     edits and ESC abandons the field (the field consumes the key first).
//   * Essential navigation needs no Fn: UP/DOWN (or ; and . via the shell's
//     fallback) move, ENTER opens, ESC goes back.
//   * Untrusted text (alert, notification or phone input) is data, never a
//     key event. There is deliberately no char -> key function here.
#pragma once
#include <stdint.h>

namespace maz {
namespace easy_keys {

// HID usage codes mirror src/core/keys.h. Keep in sync: this header stays
// standalone so the native tests can include it without the firmware tree.
enum : uint8_t {
    KEY_ENTER     = 0x28,
    KEY_ESC       = 0x29,
    KEY_BACKSPACE = 0x2A,
    KEY_SPACE     = 0x2C,
    KEY_DELETE    = 0x4C,
    KEY_L         = 0x0F,
    KEY_Y         = 0x1C,
    KEY_N         = 0x19,
    KEY_UP        = 0x52,
    KEY_DOWN      = 0x51,
    KEY_LEFT      = 0x50,
    KEY_RIGHT     = 0x4F,
};

// Explicit approve keys: Y / ENTER only. Everything else refuses.
inline bool isApproveKey(uint8_t code) {
    return code == KEY_Y || code == KEY_ENTER;
}

// Explicit deny keys: DEL (Backspace/Delete) or N. ESC never denies.
inline bool isRejectKey(uint8_t code) {
    return code == KEY_BACKSPACE || code == KEY_DELETE || code == KEY_N;
}

// "Later": leave the request pending (Core keeps it) and get out of the way.
// ESC is back, so it does this too and never denies.
inline bool isLaterKey(uint8_t code) {
    return code == KEY_L || code == KEY_ESC;
}

// On-screen key text, in one place so tests can pin it. Budgets: approval
// footer 36 chars (Font0 in the 232 px card), Home idle line 20, hints 33.
constexpr const char* kApprovalHoldFoot = "HOLD ENTER yes  DEL no  L later";
constexpr const char* kApprovalOnceFoot = "ENTER yes  DEL no  L later";
constexpr const char* kApprovalTapHint  = "hold ENTER to approve";
constexpr const char* kHomeIdleHint     = "Y: type on PC";
constexpr const char* kFlowHintIdle     = "hold SPACE to speak  ESC back";
constexpr const char* kFlowHintHold     = "release SPACE: type  ESC cancel";
constexpr const char* kFlowHintTap      = "ENTER or SPACE: type  ESC cancel";

// Essential navigation, reachable with no Fn on the Cardputer ADV.
inline bool isEasyNavKey(uint8_t code) {
    return code == KEY_UP || code == KEY_DOWN || code == KEY_LEFT ||
           code == KEY_RIGHT || code == KEY_ENTER || code == KEY_ESC ||
           code == KEY_BACKSPACE;
}

// ESC outside text entry pops back one level; inside a field the field owns
// ESC (cancel dictation / cancel edit) and the shell must not also pop.
inline bool escGoesBack(bool inTextEntry) {
    return !inTextEntry;
}

// BACKSPACE outside text entry goes back where the app offers it; inside a
// field it deletes one character. Parameterised so help/docs stay honest.
inline bool backspaceGoesBack(bool inTextEntry) {
    return !inTextEntry;
}

// Phone text never approves, whatever its contents. By construction: the
// phone path hands bytes to phone_text::applyPhoneText, which returns only
// buffer text + a Status. There is no approve path in this module.
inline bool phoneTextMayApprove() {
    return false;
}

}  // namespace easy_keys
}  // namespace maz
