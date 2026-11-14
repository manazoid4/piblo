#include "keyboard.h"

#include <Arduino.h>
#include <M5Unified.h>

namespace maz {

Keyboard KB;

// ---- TCA8418 registers (datasheet SCPS239) --------------------------------
namespace {
constexpr uint8_t REG_CFG           = 0x01;
constexpr uint8_t REG_INT_STAT      = 0x02;
constexpr uint8_t REG_KEY_LCK_EC    = 0x03;
constexpr uint8_t REG_KEY_EVENT_A   = 0x04;
constexpr uint8_t REG_GPIO_INT_EN1  = 0x1A;
constexpr uint8_t REG_KP_GPIO1      = 0x1D;
constexpr uint8_t REG_KP_GPIO2      = 0x1E;
constexpr uint8_t REG_KP_GPIO3      = 0x1F;
constexpr uint8_t REG_GPI_EM1       = 0x20;
constexpr uint8_t REG_GPIO_DIR1     = 0x23;
constexpr uint8_t REG_DEBOUNCE_DIS1 = 0x29;

constexpr uint8_t CFG_INT_CFG      = 0x10;
constexpr uint8_t CFG_OVR_FLOW_IEN = 0x08;
constexpr uint8_t CFG_KE_IEN       = 0x01;

constexpr uint32_t I2C_FREQ = 400000;

// The ADV legend is a 4x14 grid. The TCA8418 reports (row,col) in its own 7x8
// space; this is M5Stack's remap into Cardputer-compatible coordinates, kept
// behaviourally identical so third-party ADV keymaps stay comparable.
inline void remapToGrid(uint8_t& row, uint8_t& col) {
    const uint8_t r = row, c = col;
    col = static_cast<uint8_t>(r * 2 + (c > 3 ? 1 : 0));
    row = static_cast<uint8_t>((c + 4) % 4);
}

struct KeyDef {
    const char* name;
    uint8_t     code;
    char        plain;
    char        shifted;
    uint8_t     fnCode;  // KEY_NONE = no Fn override
};

const KeyDef MAP[4][14] = {
    {{"`", KEY_GRAVE, '`', '~', KEY_ESC},
     {"1", KEY_1, '1', '!', KEY_NONE},
     {"2", KEY_2, '2', '@', KEY_NONE},
     {"3", KEY_3, '3', '#', KEY_NONE},
     {"4", KEY_4, '4', '$', KEY_NONE},
     {"5", KEY_5, '5', '%', KEY_NONE},
     {"6", KEY_6, '6', '^', KEY_NONE},
     {"7", KEY_7, '7', '&', KEY_NONE},
     {"8", KEY_8, '8', '*', KEY_NONE},
     {"9", KEY_9, '9', '(', KEY_NONE},
     {"0", KEY_0, '0', ')', KEY_NONE},
     {"-", KEY_MINUS, '-', '_', KEY_NONE},
     {"=", KEY_EQUAL, '=', '+', KEY_NONE},
     {"del", KEY_BACKSPACE, 0, 0, KEY_DELETE}},
    {{"tab", KEY_TAB, '\t', '\t', KEY_NONE},
     {"q", KEY_Q, 'q', 'Q', KEY_NONE},
     {"w", KEY_W, 'w', 'W', KEY_NONE},
     {"e", KEY_E, 'e', 'E', KEY_NONE},
     {"r", KEY_R, 'r', 'R', KEY_NONE},
     {"t", KEY_T, 't', 'T', KEY_NONE},
     {"y", KEY_Y, 'y', 'Y', KEY_NONE},
     {"u", KEY_U, 'u', 'U', KEY_NONE},
     {"i", KEY_I, 'i', 'I', KEY_NONE},
     {"o", KEY_O, 'o', 'O', KEY_NONE},
     {"p", KEY_P, 'p', 'P', KEY_NONE},
     {"[", KEY_LEFTBRACE, '[', '{', KEY_NONE},
     {"]", KEY_RIGHTBRACE, ']', '}', KEY_NONE},
     {"\\", KEY_BACKSLASH, '\\', '|', KEY_NONE}},
    {{"fn", KEY_NONE, 0, 0, KEY_NONE},
     {"shift", KEY_LEFTSHIFT, 0, 0, KEY_NONE},
     {"a", KEY_A, 'a', 'A', KEY_NONE},
     {"s", KEY_S, 's', 'S', KEY_NONE},
     {"d", KEY_D, 'd', 'D', KEY_NONE},
     {"f", KEY_F, 'f', 'F', KEY_NONE},
     {"g", KEY_G, 'g', 'G', KEY_NONE},
     {"h", KEY_H, 'h', 'H', KEY_NONE},
     {"j", KEY_J, 'j', 'J', KEY_NONE},
     {"k", KEY_K, 'k', 'K', KEY_NONE},
     {"l", KEY_L, 'l', 'L', KEY_NONE},
     {";", KEY_SEMICOLON, ';', ':', KEY_UP},
     {"'", KEY_APOSTROPHE, '\'', '"', KEY_NONE},
     {"enter", KEY_ENTER, 0, 0, KEY_NONE}},
    {{"ctrl", KEY_LEFTCTRL, 0, 0, KEY_NONE},
     {"opt", KEY_LEFTMETA, 0, 0, KEY_NONE},
     {"alt", KEY_LEFTALT, 0, 0, KEY_NONE},
     {"z", KEY_Z, 'z', 'Z', KEY_NONE},
     {"x", KEY_X, 'x', 'X', KEY_NONE},
     {"c", KEY_C, 'c', 'C', KEY_NONE},
     {"v", KEY_V, 'v', 'V', KEY_NONE},
     {"b", KEY_B, 'b', 'B', KEY_NONE},
     {"n", KEY_N, 'n', 'N', KEY_NONE},
     {"m", KEY_M, 'm', 'M', KEY_NONE},
     {",", KEY_COMMA, ',', '<', KEY_LEFT},
     {".", KEY_DOT, '.', '>', KEY_DOWN},
     {"/", KEY_SLASH, '/', '?', KEY_RIGHT},
     {" ", KEY_SPACE, ' ', ' ', KEY_NONE}}};
}  // namespace

uint8_t Keyboard::reg(uint8_t r) const {
    return M5.In_I2C.readRegister8(I2C_ADDR, r, I2C_FREQ);
}
void Keyboard::reg(uint8_t r, uint8_t v) const {
    M5.In_I2C.writeRegister8(I2C_ADDR, r, v, I2C_FREQ);
}

bool Keyboard::begin() {
    if (!M5.In_I2C.scanID(I2C_ADDR, I2C_FREQ)) {
        ESP_LOGE("kb", "TCA8418 not found at 0x%02X", I2C_ADDR);
        _ok = false;
        return false;
    }

    // All expander pins to input, no GPIO interrupts — we only want key events.
    for (uint8_t i = 0; i < 3; ++i) {
        reg(REG_GPIO_DIR1 + i, 0x00);
        reg(REG_GPI_EM1 + i, 0x00);
        reg(REG_GPIO_INT_EN1 + i, 0x00);
    }

    // Claim ROWS rows and COLS columns for the key matrix, and debounce them.
    const uint32_t mask = (~(~0u << ROWS)) | ((~(~0u << COLS)) << 8);
    reg(REG_KP_GPIO1, mask & 0xFF);
    reg(REG_KP_GPIO2, (mask >> 8) & 0xFF);
    reg(REG_KP_GPIO3, (mask >> 16) & 0xFF);
    reg(REG_DEBOUNCE_DIS1, 0x00);
    reg(REG_DEBOUNCE_DIS1 + 1, 0x00);
    reg(REG_DEBOUNCE_DIS1 + 2, 0x00);

    // Drain anything the expander latched before we booted.
    for (int i = 0; i < 16 && reg(REG_KEY_EVENT_A); ++i) {
    }
    reg(REG_INT_STAT, 0x03);

    pinMode(INT_PIN, INPUT);
    reg(REG_CFG, CFG_INT_CFG | CFG_OVR_FLOW_IEN | CFG_KE_IEN);

    _ok = true;
    return true;
}

void Keyboard::push(const KeyEvent& e) {
    const uint8_t next = (_qTail + 1) % QUEUE_LEN;
    if (next == _qHead) return;  // full: drop newest rather than stall input
    _queue[_qTail] = e;
    _qTail         = next;
}

void Keyboard::inject(uint8_t code, char ch, uint8_t mods) {
    KeyEvent e;
    e.code = code; e.ch = ch; e.mods = mods; e.down = true;
    push(e);
    e.down = false;
    push(e);
}

bool Keyboard::pop(KeyEvent& out) {
    if (_qHead == _qTail) return false;
    out    = _queue[_qHead];
    _qHead = (_qHead + 1) % QUEUE_LEN;
    return true;
}

bool Keyboard::held(uint8_t code) const {
    for (uint8_t i = 0; i < _heldCount; ++i)
        if (_held[i].code == code) return true;
    return false;
}

uint32_t Keyboard::heldFor(uint8_t code) const {
    for (uint8_t i = 0; i < _heldCount; ++i)
        if (_held[i].code == code) return millis() - _held[i].since;
    return 0;
}

void Keyboard::decode(uint8_t raw) {
    const bool down = raw & 0x80;
    uint16_t   idx  = (raw & 0x7F);
    if (idx == 0) return;
    idx--;
    uint8_t row = idx / 10;
    uint8_t col = idx % 10;
    remapToGrid(row, col);
    if (row > 3 || col > 13) return;

    const KeyDef& k = MAP[row][col];

    // Fn is ours alone: it never reaches apps, it re-layers the next key.
    if (row == 2 && col == 0) {
        _fn = down;
        if (down) _mods |= MOD_FN;
        else      _mods &= ~MOD_FN;
        return;
    }

    uint8_t modBit = 0;
    switch (k.code) {
        case KEY_LEFTCTRL:  modBit = MOD_CTRL;  break;
        case KEY_LEFTSHIFT: modBit = MOD_SHIFT; break;
        case KEY_LEFTALT:   modBit = MOD_ALT;   break;
        case KEY_LEFTMETA:  modBit = MOD_META;  break;
        default: break;
    }
    if (modBit) {
        if (down) _mods |= modBit;
        else      _mods &= ~modBit;
    }

    KeyEvent e;
    e.down  = down;
    e.mods  = _mods;
    e.isMod = modBit != 0;
    e.physical = true;

    if (_fn && k.fnCode != KEY_NONE) {
        e.code = k.fnCode;
        e.ch   = 0;
    } else {
        e.code = k.code;
        e.ch   = (_mods & MOD_SHIFT) ? k.shifted : k.plain;
    }
    if (e.code == KEY_NONE) return;

    // Held-key table so apps can ask "is SPACE still down?" (push-to-talk).
    if (down) {
        bool known = false;
        for (uint8_t i = 0; i < _heldCount; ++i)
            if (_held[i].code == e.code) known = true;
        if (!known && _heldCount < HELD_MAX) {
            _held[_heldCount].code  = e.code;
            _held[_heldCount].since = millis();
            _heldCount++;
        }
    } else {
        for (uint8_t i = 0; i < _heldCount; ++i) {
            if (_held[i].code == e.code) {
                _held[i] = _held[_heldCount - 1];
                _heldCount--;
                break;
            }
        }
    }

    push(e);
}

void Keyboard::update() {
    if (!_ok) return;

    // INT is active-low and stays asserted while the FIFO has entries, so we
    // poll the line rather than run an ISR: at 60fps a keypress is still seen
    // within one frame, and no interrupt is lost across app switches.
    if (digitalRead(INT_PIN) != LOW) return;

    for (int guard = 0; guard < 16; ++guard) {
        const uint8_t raw = reg(REG_KEY_EVENT_A);
        if (raw == 0) break;
        decode(raw);
        if ((reg(REG_KEY_LCK_EC) & 0x0F) == 0) break;
    }
    reg(REG_INT_STAT, 0x03);
}

}  // namespace maz
