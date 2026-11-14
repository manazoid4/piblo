// Piblo — HID usage codes we actually care about.
//
// The ADV keyboard reports through a TCA8418 I2C matrix expander, not the
// shift-register matrix of the original Cardputer. Scan codes below follow the
// USB HID usage table so the mapping matches M5Stack's own ADV keymap
// (M5Cardputer-UserDemo, CardputerADV branch, MIT) and so a future BLE/USB HID
// bridge can forward them untouched.
#pragma once
#include <stdint.h>

namespace maz {

enum KeyMod : uint8_t {
    MOD_NONE  = 0x00,
    MOD_CTRL  = 0x01,
    MOD_SHIFT = 0x02,
    MOD_ALT   = 0x04,
    MOD_META  = 0x08,  // "opt" key on the ADV
    MOD_FN    = 0x10,  // synthesised: Fn is not a real HID modifier
};

enum KeyCode : uint8_t {
    KEY_NONE = 0x00,

    KEY_A = 0x04, KEY_B, KEY_C, KEY_D, KEY_E, KEY_F, KEY_G, KEY_H, KEY_I,
    KEY_J, KEY_K, KEY_L, KEY_M, KEY_N, KEY_O, KEY_P, KEY_Q, KEY_R, KEY_S,
    KEY_T, KEY_U, KEY_V, KEY_W, KEY_X, KEY_Y, KEY_Z,
    KEY_1 = 0x1e, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6, KEY_7, KEY_8, KEY_9, KEY_0,

    KEY_ENTER = 0x28, KEY_ESC, KEY_BACKSPACE, KEY_TAB, KEY_SPACE,
    KEY_MINUS, KEY_EQUAL, KEY_LEFTBRACE, KEY_RIGHTBRACE, KEY_BACKSLASH,
    KEY_HASHTILDE, KEY_SEMICOLON, KEY_APOSTROPHE, KEY_GRAVE,
    KEY_COMMA, KEY_DOT, KEY_SLASH, KEY_CAPSLOCK,

    KEY_DELETE = 0x4c,
    KEY_RIGHT  = 0x4f, KEY_LEFT, KEY_DOWN, KEY_UP,

    KEY_LEFTCTRL = 0xe0, KEY_LEFTSHIFT, KEY_LEFTALT, KEY_LEFTMETA,
};

struct KeyEvent {
    uint8_t code  = KEY_NONE;  // HID usage code
    char    ch    = 0;         // printable ASCII, 0 if not printable
    uint8_t mods  = MOD_NONE;  // modifier bitmask at the time of the event
    bool    down  = false;     // true = pressed, false = released
    bool    isMod = false;     // this event *is* a modifier key
    bool    physical = false;  // only the device keyboard may confirm firmware installs

    bool pressed(uint8_t c) const { return down && code == c; }
    bool released(uint8_t c) const { return !down && code == c; }
    bool ctrl() const { return mods & MOD_CTRL; }
    bool shift() const { return mods & MOD_SHIFT; }
    bool fn() const { return mods & MOD_FN; }
};

}  // namespace maz
