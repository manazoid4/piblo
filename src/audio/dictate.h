// Piblo — typing by voice.
//
// Every text field on this device is a 240px strip driven by a thumb keyboard,
// which is the slowest way to get a sentence into it. Dictation is therefore
// not a feature of one screen: it belongs to the shared TextField, so Notes,
// Tasks, Decision, Sprint, Reminders, Snippets and the command palette all
// gain it at once and behave identically.
//
// The flow is deliberately one-shot rather than streaming: record to a cache
// WAV, hand it to the host's /transcribe/raw, drop the file, return the text.
// Streaming would need a socket the firmware does not keep open, and a thought
// short enough to fit in a field is short enough to send in one piece.
#pragma once

#include <stdint.h>

#include <string>

namespace maz {
namespace dictate {

enum class State : uint8_t {
    Idle,       // nothing happening
    Listening,  // mic open, the caller should show it
    Working,    // uploaded, waiting on speech to text
    Failed,     // see error()
};

// `owner` scopes a session to one field. A screen can hold several fields
// (Decision has "what" and "why"), and only the one that started dictating may
// collect the result.
// toPc: send to the PC's /dictate?target=pc-paste instead, so the cleaned text
// is typed into whatever window is focused on the computer (Piblo Flow).
bool  start(const void* owner, bool toPc = false);
// After a toPc take(): "pasted", "clipboard" (focus moved / PC locked) or "".
const char* pcOutcome();
void  stop();    // finish recording and begin transcribing
void  cancel();  // abandon: no upload, recording discarded
void  update();  // pump from the shell loop

State       state();
bool        active(const void* owner);  // this owner is listening or working
const char* error();
float       level();  // 0..1 mic level, for the caller's meter
uint32_t    elapsedSeconds();

// True exactly once, when `owner`'s transcript is ready; `out` receives it.
bool take(const void* owner, std::string& out);

}  // namespace dictate
}  // namespace maz
