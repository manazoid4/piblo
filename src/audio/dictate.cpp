#include "dictate.h"

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <upload_gate.h>  // firmware/nod/include (pure upload state machine)

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
// The HTTP call gives up after HTTP_MS; the UI gives up slightly later so a slow
// PC never leaves the screen stuck. Same stack as the host worker (measured safe).
constexpr uint32_t HTTP_MS = 25000;
constexpr uint32_t UI_MS = 28000;
constexpr uint32_t UPLOAD_STACK = 9216;

nod::UploadGate gGate;
bool            gSent = false;   // upload task launched for the current Working state
std::string     gJobPath;        // worker-owned copy: the task deletes the file when done
Target          gJobTarget = Target::Field;
host::Reply     gJobReply;       // written by the task before gGate.finish()

void uploadTask(void*) {
    const auto reply = gJobTarget == Target::Craft ? host::craftVoice(gJobPath, HTTP_MS)
                     : gJobTarget == Target::Pc ? host::boardFlowPaste(gJobPath, HTTP_MS)
                     : host::boardTranscribe(gJobPath, HTTP_MS);
    store::remove(gJobPath);
    gJobReply = reply;
    gGate.finish();
    vTaskDelete(nullptr);
}

State               gState = State::Idle;
const void*         gOwner = nullptr;
voice::WavFileSink* gSink  = nullptr;
std::string         gPath;
std::string         gText;
std::string         gError;
std::string         gPcOutcome;
Target              gTarget = Target::Field;
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

bool start(const void* owner, bool toPc, bool craft) {
    if (gState == State::Listening || gState == State::Working) return false;
    if (gGate.busy()) {  // an abandoned upload is still on the wire
        fail("still sending the last one - wait");
        return false;
    }
    if (!store::ready()) {
        fail("no storage for the recording");
        return false;
    }
    gText.clear();
    gError.clear();
    gPcOutcome.clear();
    gTarget = toPc ? targetForPc(craft) : Target::Field;
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
    gSent = false;
    // One frame of grace so "Sending" is on screen before the task starts.
    gWorkAt = millis() + 120;
}

void cancel() {
    if (gState == State::Listening) voice::stop();
    if (gSink) {
        gPath = gSink->path();
        delete gSink;
        gSink = nullptr;
    }
    if (gState == State::Working && gSent) {
        // The task owns (and deletes) the file; just stop waiting for it.
        gPath.clear();
        if (!gGate.abandon()) gGate.take();  // finished a moment ago: drop the result
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

    if (!gSent) {
        if (!Sys.wifiConnected || !host::configured()) {
            fail("no host - connect Wi-Fi to dictate");
            return;
        }
        if (!gGate.begin(millis())) {
            fail("still sending the last one - wait");
            return;
        }
        // Only touch the job slot once we own the gate: an abandoned task may still use it.
        gJobPath = gPath;
        gJobTarget = gTarget;
        gJobReply = host::Reply{};
        if (xTaskCreatePinnedToCore(uploadTask, "pibloDict", UPLOAD_STACK, nullptr, 1,
                                    nullptr, 0) != pdPASS) {
            gGate.abort();
            fail("low memory - try again");
            return;
        }
        gSent = true;
        return;
    }
    if (!gGate.take()) {
        if (gGate.timedOut(millis(), UI_MS) && gGate.abandon()) {  // else it just finished: next tick
            gPath.clear();  // the task deletes the file
            fail(gTarget == Target::Pc ? "PC too slow - it may still type" : "PC did not answer in time");
        }
        return;
    }
    const auto reply = gJobReply;
    gPath.clear();
    if (!reply.ok) {
        fail(reply.error.empty() ? "host could not transcribe"
                                 : reply.error.c_str());
        return;
    }
    if (reply.transcript.empty() && gTarget != Target::Craft) {
        fail("nothing was recognised");
        return;
    }
    gText  = reply.transcript;
    gPcOutcome = gTarget == Target::Craft ? "craft" : (gTarget == Target::Pc ? reply.text : "");
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
uint32_t    sendingMs() { return gState == State::Working && gSent ? millis() - gWorkAt : 0; }

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
