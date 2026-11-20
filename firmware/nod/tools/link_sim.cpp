// Headless soak of Piblo Link over a simulated UART (no hardware): two Links joined by byte
// pipes that are rate-limited like a real UART (10 bits/byte), with optional bit errors, junk
// bytes and a cable pull. Prints numbers and exits non-zero if any invariant breaks:
// every delivered DATA frame is intact, and the link recovers by itself.
//   make link-sim            (baud 115200 and 921600, ~30 s of simulated time each)
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <deque>
#include <map>
#include "../include/piblo_link.h"
#include "../include/bench_stats.h"
using namespace nod::link;

static uint32_t rng = 12345;
static uint32_t rnd() { rng = rng * 1664525u + 1013904223u; return rng >> 8; }

struct Wire {  // one direction; releases bytes at the UART rate
    std::deque<uint8_t> q; double credit = 0;
    bool cut = false; double ber = 0; double junk = 0;  // per-byte corrupt / junk-insert odds
};

struct Result { bool ok = true; };
static int bad = 0;
#define EXPECT(c) do { if (!(c)) { bad++; printf("  FAIL line %d: %s\n", __LINE__, #c); } } while (0)

static void fillPayload(uint8_t* p, size_t len, uint32_t id) {  // [id u32][len-4 bytes derived from id]
    memcpy(p, &id, 4);
    for (size_t i = 4; i < len; i++) p[i] = (uint8_t)(id * 31 + i * 7);
}
static bool checkPayload(const uint8_t* p, size_t len, uint32_t* idOut) {
    if (len < 4) return false;
    uint32_t id; memcpy(&id, p, 4); *idOut = id;
    for (size_t i = 4; i < len; i++) if (p[i] != (uint8_t)(id * 31 + i * 7)) return false;
    return true;
}

struct Run {
    Link a, b; Wire ab, ba; double bytesPerMs;
    uint32_t now = 0, nextId = 1, delivered = 0, corrupt = 0, dupOrBackwards = 0, lastId = 0;
    uint64_t payloadBytes = 0, sentBytes = 0;
    std::map<uint32_t, uint32_t> sentAt; nod::BenchStat lat;
    uint32_t upSince = 0; bool wasUp = false; nod::BenchStat upTime;

    explicit Run(int baud) { bytesPerMs = baud / 10.0 / 1000.0; }

    void wire(Wire& w, Link& from, Link& to) {
        uint8_t t[64]; size_t k;
        while ((k = from.pullTx(t, sizeof t)) > 0) for (size_t i = 0; i < k; i++) {
            if (w.cut) continue;                       // bytes into an unplugged cable vanish
            uint8_t c = t[i];
            if (w.ber > 0 && (rnd() % 1000000) < w.ber * 1e6) c ^= (uint8_t)(1u << (rnd() & 7));
            w.q.push_back(c);
            if (w.junk > 0 && (rnd() % 1000000) < w.junk * 1e6) w.q.push_back((uint8_t)rnd());
        }
        w.credit += bytesPerMs;
        while (w.credit >= 1 && !w.q.empty()) { to.onByte(w.q.front(), now); w.q.pop_front(); w.credit -= 1; }
        if (w.q.empty() && w.credit > 1) w.credit = 1;  // idle line does not bank capacity
    }
    void step(bool sendData) {
        now++;
        a.tick(now); b.tick(now);
        if (sendData) {                                // A streams as fast as credit allows
            const size_t len = 16 + rnd() % (MAX_PAYLOAD - 16 + 1);
            uint8_t p[MAX_PAYLOAD]; fillPayload(p, len, nextId);
            if (a.send(0x11, p, len)) { sentAt[nextId] = now; nextId++; sentBytes += len; }
        }
        wire(ab, a, b); wire(ba, b, a);
        Frame f;
        while (b.recv(f)) {
            uint32_t id;
            if (!checkPayload(f.payload, f.len, &id)) { corrupt++; continue; }
            if (id <= lastId) dupOrBackwards++;
            lastId = id; delivered++; payloadBytes += f.len;
            auto it = sentAt.find(id); if (it != sentAt.end()) lat.add(now - it->second);
        }
        const bool up = a.useCoprocessor() && b.useCoprocessor();
        if (up && !wasUp) upSince = now;
        wasUp = up;
    }
    bool bothUp() const { return a.useCoprocessor() && b.useCoprocessor(); }
};

static void report(const char* name, int baud, const Run& r, uint32_t ms) {
    char l[64]; nod::benchFormat(l, sizeof l, "lat_ms", r.lat);
    printf("[link] %-22s baud=%d t=%ums frames=%u payload_Bps=%.0f (wire max %.0f) %s crcErr=%u resync=%u seqGaps=%u noCredit=%u fallbacks=%u reconnects=%u\n",
           name, baud, ms, r.delivered, r.payloadBytes * 1000.0 / ms, baud / 10.0, l,
           r.b.parser.crcErrors, r.b.parser.resyncBytes, r.b.seqGaps, r.a.noCredit, r.a.fallbacks, r.a.reconnects);
}

int main() {
    const int bauds[] = {115200, 921600};
    for (int baud : bauds) {
        printf("== %d baud ==\n", baud);
        { // 1. clean line, saturated sender
            Run r(baud); for (int i = 0; i < 1000; i++) r.step(false);
            EXPECT(r.bothUp());
            const uint32_t t0 = r.now; r.payloadBytes = 0; r.delivered = 0;
            for (int i = 0; i < 10000; i++) r.step(true);
            report("clean", baud, r, r.now - t0);
            EXPECT(r.corrupt == 0 && r.dupOrBackwards == 0 && r.b.parser.crcErrors == 0 && r.b.seqGaps == 0);
            EXPECT(r.payloadBytes * 1000.0 / (r.now - t0) > 0.5 * baud / 10.0);  // >50% of wire rate is payload
        }
        { // 2. noisy line: bit errors + junk bytes. Frames may be lost, never delivered damaged.
            Run r(baud); r.ab.ber = r.ba.ber = 1e-3; r.ab.junk = r.ba.junk = 1e-3;
            for (int i = 0; i < 30000; i++) r.step(true);
            report("noisy ber=1e-3", baud, r, r.now);
            EXPECT(r.corrupt == 0 && r.b.parser.crcErrors > 0 && r.delivered > 0);
            EXPECT(r.bothUp());                         // still linked and still moving data at the end
            const uint32_t before = r.delivered;
            r.ab.ber = r.ba.ber = r.ab.junk = r.ba.junk = 0;
            for (int i = 0; i < 3000; i++) r.step(true);
            EXPECT(r.delivered > before + 10);          // recovers throughput once the noise stops
        }
        { // 3. cable pulled 3 s then replugged, then 1 s power-cycle of one side
            Run r(baud); for (int i = 0; i < 1000; i++) r.step(true);
            EXPECT(r.bothUp());
            r.ab.cut = r.ba.cut = true; for (int i = 0; i < 3000; i++) r.step(true);
            EXPECT(!r.a.useCoprocessor() && !r.b.useCoprocessor() && r.a.fallbacks >= 1);  // fallback active
            EXPECT(!r.a.send(0x11, (const uint8_t*)"x", 1));
            r.ab.cut = r.ba.cut = false; const uint32_t t0 = r.now;
            while (!r.bothUp() && r.now - t0 < 5000) r.step(true);
            printf("[link] %-22s baud=%d reconnect_after_replug_ms=%u\n", "unplug/replug", baud, r.now - t0);
            EXPECT(r.bothUp() && r.now - t0 <= 1500);
            const uint32_t d0 = r.delivered; for (int i = 0; i < 1000; i++) r.step(true);
            EXPECT(r.delivered > d0);
            r.b = Link(); const uint32_t t1 = r.now;   // B power-cycles (loses all state)
            for (int i = 0; i < 2000; i++) r.step(true);
            printf("[link] %-22s baud=%d b_reboot_to_data_ms<=2000 ok=%d\n", "peer reboot", baud, (int)r.bothUp());
            EXPECT(r.bothUp() && r.now > t1);
        }
    }
    printf(bad ? "link_sim: %d FAILED\n" : "link_sim: all checks passed\n", bad);
    return bad ? 1 : 0;
}
