// The Talk circle: one drawing routine shared by Home (big) and Call (small).
#pragma once
#include <M5Unified.h>

#include <talk_home.h>  // firmware/nod/include (pure state + animation maths)

namespace maz {
namespace ui {

// micLevel 0..1 (voice::level()). `label`/`sub` are drawn inside when r >= 30.
void talkOrb(M5Canvas& g, int cx, int cy, int r, ::nod::TalkState s, float micLevel,
             bool focused = false, const char* label = nullptr, const char* sub = nullptr);

}  // namespace ui
}  // namespace maz
