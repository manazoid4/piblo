#include "bench.h"

#ifdef PIBLO_BENCH
#include <Arduino.h>
#include <bench_stats.h>  // firmware/nod/include

#include "../audio/voice.h"

namespace maz {
namespace bench {
namespace {
nod::BenchStat gLoop, gNet, gVoice;
uint32_t gBootMs = 0, gAudioGaps = 0, gLastReport = 0;
constexpr uint32_t LOOP_OVER_MS = 100;  // a visible hitch
constexpr uint32_t REPORT_MS = 30000;
}  // namespace

void bootDone() { gBootMs = millis(); }

void loopMs(uint32_t ms) {
    gLoop.add(ms, LOOP_OVER_MS);
    // PROXY, not a real underrun counter (the audio sinks expose none): a >100 ms loop while
    // the mic/speaker is live is where the stream could have glitched.
    if (ms > LOOP_OVER_MS && (voice::state() == voice::State::Listening ||
                              voice::state() == voice::State::Playing))
        gAudioGaps++;
}
void netWaitMs(uint32_t ms) { gNet.add(ms); }
void voiceResponseMs(uint32_t ms) { gVoice.add(ms); }

void report() {
    const uint32_t now = millis();
    if (now - gLastReport < REPORT_MS) return;
    gLastReport = now;
    char loop[64], net[64], voiceS[64];
    nod::benchFormat(loop, sizeof loop, "loop_ms", gLoop);
    nod::benchFormat(net, sizeof net, "net_ms", gNet);
    nod::benchFormat(voiceS, sizeof voiceS, "voice_ms", gVoice);
    Serial.printf("[bench] t=%lu boot_ms=%lu %s %s %s audio_gaps=%lu\n", (unsigned long)now,
                  (unsigned long)gBootMs, loop, net, voiceS, (unsigned long)gAudioGaps);
    gLoop.reset(); gNet.reset(); gVoice.reset(); gAudioGaps = 0;
}
}  // namespace bench
}  // namespace maz
#endif
