#pragma once

#include <stdint.h>

// Benchmark harness: timing counters printed on serial as "[bench] ..." lines next to the
// existing "[health] ..." line, parsed by scripts/bench_parse.py. Compiled in ONLY with
// -DPIBLO_BENCH (env cardputer-adv-bench); the normal build gets empty macros (zero cost).
#ifdef PIBLO_BENCH
namespace maz {
namespace bench {
void bootDone();                   // end of setup(): records boot_ms
void loopMs(uint32_t ms);          // one main-loop pass (UI latency)
void netWaitMs(uint32_t ms);       // one blocking/background HTTP exchange
void voiceResponseMs(uint32_t ms); // key release -> text/answer ready
void report();                     // prints every 30 s, resets the window
}  // namespace bench
}  // namespace maz
#define PIBLO_BENCH_BOOT() ::maz::bench::bootDone()
#define PIBLO_BENCH_LOOP(ms) ::maz::bench::loopMs(ms)
#define PIBLO_BENCH_NET(ms) ::maz::bench::netWaitMs(ms)
#define PIBLO_BENCH_VOICE(ms) ::maz::bench::voiceResponseMs(ms)
#define PIBLO_BENCH_REPORT() ::maz::bench::report()
#else
#define PIBLO_BENCH_BOOT() ((void)0)
#define PIBLO_BENCH_LOOP(ms) ((void)0)
#define PIBLO_BENCH_NET(ms) ((void)0)
#define PIBLO_BENCH_VOICE(ms) ((void)0)
#define PIBLO_BENCH_REPORT() ((void)0)
#endif
