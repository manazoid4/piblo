#include "character.h"

#include "chars/green.h"
#include "chars/green_small.h"

namespace maz {
namespace ui {

static_assert(CHAR_SMALL_W == chars::GREEN_SMALL_W && CHAR_SMALL_H == chars::GREEN_SMALL_H, "regenerate character.h sizes");
static_assert(CHAR_W == chars::GREEN_W && CHAR_H == chars::GREEN_H, "regenerate character.h sizes");

void character(M5Canvas& g, int x, int y, nod::TalkState s, float micLevel, uint32_t ms) {
    int dy = 0, dx = 0;
    switch (s) {
        case nod::TALK_LISTENING: dy = -static_cast<int>(micLevel * 4.f); break;   // perks up with your voice
        case nod::TALK_SPEAKING:  dy = (ms / 160) & 1 ? -2 : 0; break;             // chatty bounce
        case nod::TALK_THINKING:  dx = (ms / 400) & 1 ? 1 : -1; break;             // ponder sway
        default:                  dy = (ms / 900) & 1 ? -1 : 0; break;             // breathing
    }
    g.pushImage(x + dx, y + dy + 2, CHAR_W, CHAR_H, chars::GREEN, chars::GREEN_KEY);
}

void characterSmall(M5Canvas& g, int x, int y, uint32_t ms) {
    g.pushImage(x, y + ((ms / 900) & 1 ? 0 : 1), CHAR_SMALL_W, CHAR_SMALL_H, chars::GREEN_SMALL, chars::GREEN_SMALL_KEY);
}

}  // namespace ui
}  // namespace maz
