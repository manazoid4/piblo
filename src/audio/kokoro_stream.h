// Piblo — stream TTS from a Kokoro server on the PC (raw 16-bit mono 24 kHz PCM
// over HTTP) straight into the speaker. Blocks until done or any key is pressed.
#pragma once
#include <Arduino.h>

namespace maz {
namespace kokoro {
// false: no Wi-Fi, HTTP failure, or no audio. true: played (or cut by a key press).
bool speak(const String& text);
inline bool speakFromKokoro(const String& text) { return speak(text); }
}  // namespace kokoro
}  // namespace maz
