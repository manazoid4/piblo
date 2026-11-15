// Piblo's character (the green hooded one), drawn from a generated RGB565 sprite.
#pragma once
#include <M5Unified.h>

#include <talk_home.h>

namespace maz {
namespace ui {

constexpr int CHAR_W = 29, CHAR_H = 44;  // matches src/ui/chars/green.h

// Top-left at x,y. Bobs to show the Talk state; listening follows the mic level.
void character(M5Canvas& g, int x, int y, nod::TalkState s, float micLevel, uint32_t ms);

// Small breathing version for the Home status line (fits its 36 px band).
constexpr int CHAR_SMALL_W = 17, CHAR_SMALL_H = 26;  // matches src/ui/chars/green_small.h
void characterSmall(M5Canvas& g, int x, int y, uint32_t ms);

}  // namespace ui
}  // namespace maz
