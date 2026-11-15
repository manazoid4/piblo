#include "dictate.h"

#include <Arduino.h>

#include "../core/sys.h"
#include "../net/mazhost.h"
#include "../storage/store.h"
#include "sfx.h"
#include "voice.h"

namespace maz {
namespace dictate {
namespace {

// A field holds a line, not a monologue. Capping at 30s keeps the upload small
// enough to feel instant and stops a pocket-pressed key filling the card.
constexpr uint32_t MAX_SECONDS = 30;

State               gState = State::Idle;
const void*         gOwner = nullptr;
voice::WavFileSink* gSink  = nullptr;
std::string         gPath;
std::string         gText;
std::string         gError;
std::string         gPcOutcome;
bool                gToPc   = false;
bool                gReady  = false;
uint32_t            gWorkAt = 0;

void discard() {
    if (!gPath.empty()) {
        store::remove(gPath);
        gPath.clear();
    }
}

void fail(const char* why) {
    gError = why ? why : "dictation failed";
    gState = State::Failed;
    gOwner = nullptr;
    gReady = false;
    discard();
    sfx::error();
}

}  // namespace

bool start(const void* owner, bool toPc) {
    if (gState == State::Listening || gState == State::Working) return false;
    if (!store::ready()) {
        fail("no storage for the recording");
        return false;
    }
    gText.clear();
    gError.clear();
    gPcOutcome.clear();
    gToPc  = toPc;
    gReady = false;
    discard();

    gSink = new voice::WavFileSink("cache");
    if (!voice::start(gSink, MAX_SECONDS)) {
        delete gSink;
        gSink = nullptr;
        fail(voice::lastError());
        return false;
    }
    gOwner = owner;
    gState = State::Listening;
    sfx::recStart();
    return true;
}

void stop() {
    if (gState != State::Listening) return;
    voice::stop();
    if (gSink) {
        gPath = gSink->path();
        delete gSink;
        gSink = nullptr;
    }
    sfx::recStop();
    if (gPath.empty()) {
        fail("nothing was recorded");
        return;
    }
    gState = State::Working;
    // One frame of grace so "WORKING" is on screen before the blocking upload.
    gWorkAt = millis() + 120;
}

void cancel() {
    if (gState == State::Listening) voice::stop();
    if (gSink) {
        gPath = gSink->path();
        delete gSink;
        gSink = nullptr;
    }
    discard();
    gState = State::Idle;
    gOwner = nullptr;
    gReady = false;
}

void update() {
    // The cap can end a recording without anyone pressing anything.
    if (gState == State::Listening &&
        voice::state() != voice::State::Listening) {
        stop();
        return;
    }
    if (gState != State::Working || millis() < gWorkAt) return;

    if (!Sys.wifiConnected || !host::configured()) {
        fail("no host - connect Wi-Fi to dictate");
        return;
    }
    const auto reply = gToPc ? host::flowPaste(gPath) : host::transcribe(gPath);
    discard();
    if (!reply.ok) {
        fail(reply.error.empty() ? "host could not transcribe"
                                 : reply.error.c_str());
        return;
    }
    if (reply.transcript.empty()) {
        fail("nothing was recognised");
        return;
    }
    gText  = reply.transcript;
    gPcOutcome = gToPc ? reply.text : "";
    gReady = true;
    gState = State::Idle;
    sfx::confirm();
}

State state() { return gState; }

bool active(const void* owner) {
    return gOwner == owner &&
           (gState == State::Listening || gState == State::Working);
}

const char* error() { return gError.c_str(); }
const char* pcOutcome() { return gPcOutcome.c_str(); }
float       level() { return voice::level(); }
uint32_t    elapsedSeconds() { return voice::elapsedSeconds(); }

bool take(const void* owner, std::string& out) {
    if (!gReady || owner != gOwner) return false;
    out    = gText;
    gReady = false;
    gOwner = nullptr;
    gText.clear();
    return true;
}

}  // namespace dictate
}  // namespace maz
