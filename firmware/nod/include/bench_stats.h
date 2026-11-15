// Tiny running statistics for the on-device benchmark harness (host-testable).
// One Stat = count/min/mean/max plus how many samples were over a limit.
#pragma once

#include <stdint.h>
#include <stdio.h>

namespace nod {

struct BenchStat {
    uint32_t count = 0, minV = 0, maxV = 0, over = 0;
    uint64_t sum = 0;

    void add(uint32_t v, uint32_t overLimit = 0xFFFFFFFFu) {
        if (count == 0 || v < minV) minV = v;
        if (v > maxV) maxV = v;
        if (v > overLimit) over++;
        sum += v;
        count++;
    }
    uint32_t mean() const { return count ? (uint32_t)(sum / count) : 0; }
    void reset() { *this = BenchStat(); }
};

// "name=count/min/mean/max/over" (the format scripts/bench_parse.py reads).
inline int benchFormat(char* out, size_t cap, const char* name, const BenchStat& s) {
    return snprintf(out, cap, "%s=%lu/%lu/%lu/%lu/%lu", name, (unsigned long)s.count,
                    (unsigned long)s.minV, (unsigned long)s.mean(), (unsigned long)s.maxV,
                    (unsigned long)s.over);
}

}  // namespace nod
