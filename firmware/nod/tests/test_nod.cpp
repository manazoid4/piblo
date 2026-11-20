#include <cstdio>
#include <cstdlib>
#include <cmath>
#include "../include/alert_policy.h"
#include "../include/approval_format.h"
#include "../include/charge_monitor.h"
#include "../include/poll_backoff.h"
#include "../include/nod_hold.h"
#include "../include/tone_seq.h"
#include "../include/easy_keys.h"
#include "../include/home_layout.h"
#include "../include/phone_text.h"
#include "../include/talk_home.h"
#include "../include/nod_theme.h"
#include "../include/safe_file.h"
#include "../include/pair_connect.h"
#include "../include/fw_version.h"
#include "../include/upload_gate.h"
#include "../include/bench_stats.h"
#include "../include/piblo_link.h"
#include <map>
#include <string>
using namespace nod;
static int fails = 0, checks = 0;
#define CHECK(c) do { checks++; if (!(c)) { fails++; printf("FAIL line %d: %s\n", __LINE__, #c); } } while (0)

static void feedRun(ChargeMonitor& m, uint32_t& t, int startMv, float mvPerMin, int minutes, int vbus = -1) {
    for (int s = 0; s < minutes * 6; s++) {
        t += 10000;
        m.feed(t, startMv + (int)(mvPerMin * (s / 6.0f)), vbus);
    }
}

// In-memory FS for safe_file.h. failRename makes the Nth rename fail (restart between renames).
struct FakeFs {
    std::map<std::string, std::string> files;
    int renames = 0, failRename = -1;
    bool exists(const char* p) { return files.count(p) != 0; }
    bool remove(const char* p) { return files.erase(p) != 0; }
    bool rename(const char* a, const char* b) {
        if (renames++ == failRename || !files.count(a)) return false;
        files[b] = files[a]; files.erase(a); return true;
    }
};

int main() {
    // firmware version guard: only strictly newer staged images install
    CHECK(fwIsNewer("1.6.0", "1.5.1")); CHECK(fwIsNewer("1.10.0", "1.9.9")); CHECK(fwIsNewer("v2.0", "1.9.9"));
    CHECK(!fwIsNewer("1.5.1", "1.6.0")); CHECK(!fwIsNewer("1.6.0", "1.6.0")); CHECK(!fwIsNewer("1.6.0-beta", "1.6.0"));
    CHECK(!fwIsNewer("", "1.5.1")); CHECK(!fwIsNewer("bad", "1.5.1")); CHECK(!fwIsNewer("1.6.0", "")); CHECK(!fwIsNewer(nullptr, "1.0.0"));

    // upload gate: background Flow upload state machine
    {
        UploadGate g;
        CHECK(g.state() == UploadGate::Idle && !g.busy());
        CHECK(!g.take());                                   // nothing to take
        CHECK(g.begin(1000)); CHECK(!g.begin(1001));        // no double-send
        CHECK(!g.timedOut(1000 + 24999, 25000)); CHECK(g.timedOut(1000 + 25000, 25000));
        CHECK(!g.take());                                   // not finished yet
        g.finish(); CHECK(g.state() == UploadGate::Done);   // success path
        CHECK(!g.begin(2000));                              // result not collected yet
        CHECK(g.take()); CHECK(!g.busy()); CHECK(!g.take());
        // timeout: UI gives up, worker still alive -> stays busy, then frees itself
        CHECK(g.begin(0xFFFFFF00u)); CHECK(g.timedOut(0x100u, 0x200u));  // millis() wrap
        CHECK(g.abandon()); CHECK(g.busy()); CHECK(!g.timedOut(0x1000, 1));
        CHECK(!g.begin(5)); CHECK(!g.take());               // re-entry refused, no stale result
        g.finish(); CHECK(g.state() == UploadGate::Idle); CHECK(g.begin(6));
        // cancel after the worker already finished: result dropped, gate free
        g.finish(); CHECK(!g.abandon()); CHECK(g.state() == UploadGate::Done); CHECK(g.take()); CHECK(!g.busy());
        // worker failed to start
        CHECK(g.begin(7)); g.abort(); CHECK(!g.busy());
        // repeated use does not wedge
        for (int i = 0; i < 200; i++) { CHECK(g.begin(i)); if (i % 3 == 0) g.abandon(); g.finish(); g.take(); CHECK(!g.busy()); }
    }

    // bench stats
    {
        BenchStat s; CHECK(s.mean() == 0);
        s.add(10, 100); s.add(300, 100); s.add(5, 100);
        CHECK(s.count == 3 && s.minV == 5 && s.maxV == 300 && s.over == 1 && s.mean() == 105);
        char b[64]; benchFormat(b, sizeof b, "loop_ms", s);
        CHECK(std::string(b) == "loop_ms=3/5/105/300/1");
        s.reset(); CHECK(s.count == 0 && s.maxV == 0);
    }

    // piblo link: framed UART transport
    {
        using namespace nod::link;
        const uint8_t chk[] = {'1','2','3','4','5','6','7','8','9'};
        CHECK(crc16(chk, 9) == 0x29B1);  // CRC-16/CCITT-FALSE check value

        // round trip + corrupt + partial + bad length + version mismatch
        uint8_t buf[MAX_FRAME]; const uint8_t pl[3] = {1, 2, 3};
        const size_t n = encode(0x10, 7, pl, 3, buf, sizeof buf);
        CHECK(n == 11); CHECK(encode(0x10, 0, pl, MAX_PAYLOAD + 1, buf, sizeof buf) == 0);
        Parser p; Frame f; bool got = false;
        for (size_t i = 0; i < n; i++) got = p.feed(buf[i], f);
        CHECK(got && f.type == 0x10 && f.seq == 7 && f.len == 3 && f.payload[2] == 3);

        uint8_t bad[MAX_FRAME]; memcpy(bad, buf, n); bad[7] ^= 0x40;       // flip a payload bit
        got = false; for (size_t i = 0; i < n; i++) got |= p.feed(bad[i], f);
        CHECK(!got && p.crcErrors == 1);
        got = false; for (size_t i = 0; i < n; i++) got |= p.feed(buf[i], f);  // recovers on next frame
        CHECK(got);

        got = false;                                                         // partial + garbage prefix
        p.feed(0x00, f); p.feed(0x13, f);
        for (size_t i = 0; i < 5; i++) got |= p.feed(buf[i], f);
        CHECK(!got);
        for (size_t i = 5; i < n; i++) got |= p.feed(buf[i], f);
        CHECK(got && p.resyncBytes >= 2);

        const uint8_t longHdr[] = {SOF, VERSION, 0x10, 0, (uint8_t)((MAX_PAYLOAD + 1) & 0xFF), 0};
        for (size_t i = 0; i < sizeof longHdr; i++) p.feed(longHdr[i], f);
        CHECK(p.badLength == 1);

        // two links wired back to back; `drop` lets a test lose frames in transit
        Link a, b; uint32_t now = 0;
        auto pump = [&](bool cutAtoB, bool cutBtoA) {
            uint8_t w[64]; size_t k;
            a.tick(now); b.tick(now);
            while ((k = a.pullTx(w, sizeof w)) > 0) if (!cutAtoB) for (size_t i = 0; i < k; i++) b.onByte(w[i], now);
            while ((k = b.pullTx(w, sizeof w)) > 0) if (!cutBtoA) for (size_t i = 0; i < k; i++) a.onByte(w[i], now);
        };
        auto run = [&](int ms, bool cut) { for (int t = 0; t < ms; t += 50) { now += 50; pump(cut, cut); } };
        CHECK(!a.useCoprocessor() && a.state() == Down);                     // fallback until handshake
        run(1000,false);
        CHECK(a.useCoprocessor() && b.useCoprocessor() && a.credits() == RX_SLOTS && a.reconnects == 1);

        // flow control: 4 credits, 5th refused, credit returns when the peer drains
        const uint8_t d[1] = {9};
        for (int i = 0; i < 4; i++) CHECK(a.send(0x11, d, 1));
        CHECK(!a.send(0x11, d, 1) && a.noCredit == 1);
        run(100,false);
        Frame r; int got4 = 0; while (b.recv(r)) { got4++; CHECK(r.type == 0x11 && r.payload[0] == 9); }
        CHECK(got4 == 4);
        run(200,false);
        CHECK(a.credits() == RX_SLOTS && a.send(0x11, d, 1));
        CHECK(!a.send(0x02, d, 1));                                          // control types are not app data
        run(100,false); while (b.recv(r)) {}  run(200,false);

        // seq gap: first DATA lost on the wire, second arrives
        a.send(0x12, d, 1); pump(true, false);                               // lost
        a.send(0x12, d, 1); pump(false,false);
        CHECK(b.seqGaps == 1 && b.recv(r));
        run(200,false);
        // the lost frame leaked one credit; it is only given back once the sender is stuck at 0
        CHECK(a.credits() == RX_SLOTS - 1 && a.creditResyncs == 0);
        run(1200,false);
        CHECK(a.credits() == RX_SLOTS - 1 && a.creditResyncs == 0);
        for (int i = 0; i < (int)RX_SLOTS; i++) a.send(0x12, d, 1);
        pump(true, false);                                       // whole burst lost
        run(1500,false);
        CHECK(a.credits() == RX_SLOTS && a.creditResyncs == 1 && b.state() == Up && a.state() == Up);

        // overflow: a misbehaving peer ignores credit -> bounded RX queue drops, counts, no crash
        {
            Link c; uint8_t w[MAX_FRAME]; uint8_t seq = 0;
            const size_t hn = encode(HELLO, seq++, (const uint8_t*)"\x04\x01", 2, w, sizeof w);
            for (size_t i = 0; i < hn; i++) c.onByte(w[i], 0);
            CHECK(c.useCoprocessor());
            for (int i = 0; i < 6; i++) {
                const size_t m = encode(0x20, seq++, d, 1, w, sizeof w);
                for (size_t j = 0; j < m; j++) c.onByte(w[j], 0);
            }
            CHECK(c.rxOverflow == 2);
            int kept = 0; while (c.recv(r)) kept++;
            CHECK(kept == (int)RX_SLOTS);
        }
        // tx overflow: big frames fill the bounded wire queue before it is drained
        {
            uint8_t big[MAX_PAYLOAD]; memset(big, 0x5A, sizeof big);
            int ok = 0; for (int i = 0; i < 4; i++) ok += a.send(0x13, big, sizeof big);
            CHECK(ok == 2 && a.txOverflow >= 1);
            run(200,false); while (b.recv(r)) {} run(300,false);
        }

        // version mismatch: peer speaks v2 -> Incompatible -> fallback, recovers on a v1 frame
        {
            uint8_t w[MAX_FRAME]; const size_t m = encode(HEARTBEAT, 0, nullptr, 0, w, sizeof w);
            w[1] = VERSION + 1;
            const uint32_t fb = a.fallbacks;
            for (size_t i = 0; i < m; i++) a.onByte(w[i], now);
            CHECK(a.state() == Incompatible && !a.useCoprocessor() && a.fallbacks == fb + 1);
            CHECK(a.parser.versionMismatch == 1 && !a.send(0x11, d, 1));
            run(1500,false);
            CHECK(a.useCoprocessor());                                       // re-handshakes by itself
        }

        // disconnect / reconnect: silence -> Down -> fallback; wires back -> Up again
        const uint32_t fb2 = a.fallbacks, rc = a.reconnects;
        run(1500, true);
        CHECK(a.state() == Down && b.state() == Down && !a.send(0x11, d, 1));
        CHECK(a.fallbacks == fb2 + 1 && b.fallbacks >= 1);
        run(1500,false);
        CHECK(a.useCoprocessor() && b.useCoprocessor() && a.reconnects == rc + 1 && a.send(0x11, d, 1));
        run(200,false); while (b.recv(r)) {} run(200,false);

        // peer reboot: fresh Link B2 says HELLO, A resets seq/credits instead of counting gaps
        const uint32_t gaps = a.seqGaps;
        b = Link(); run(1500,false);
        CHECK(a.useCoprocessor() && b.useCoprocessor() && a.seqGaps == gaps && a.credits() == RX_SLOTS);
    }

    // battery curve
    CHECK(voltageToPercent(3000) == 0); CHECK(voltageToPercent(4300) == 100);
    CHECK(voltageToPercent(3840) == 50);
    for (int mv = 3000; mv < 4300; mv += 5) CHECK(voltageToPercent(mv) <= voltageToPercent(mv + 5)); // monotonic

    // rising voltage, VBUS unknown => Charging (the M5Launcher case)
    { ChargeMonitor m; uint32_t t = 0; feedRun(m, t, 3700, 6.0f, 5);
      CHECK(m.state() == PowerState::Charging); CHECK(m.etaMinutes() > 0); CHECK(m.percent() <= 99); }
    // falling voltage => OnBattery
    { ChargeMonitor m; uint32_t t = 0; feedRun(m, t, 3900, -3.0f, 5);
      CHECK(m.state() == PowerState::OnBattery); CHECK(m.etaMinutes() == -1); }
    // too little data => stays Unknown, never guesses
    { ChargeMonitor m; uint32_t t = 0; feedRun(m, t, 3700, 6.0f, 1);
      CHECK(m.state() == PowerState::Unknown); }
    // noise on a flat battery must not flip to Charging
    { ChargeMonitor m; uint32_t t = 0; for (int i = 0; i < 60; i++) { t += 10000; m.feed(t, 3800 + (i % 2 ? 4 : -4)); }
      CHECK(m.state() != PowerState::Charging); }
    // full detection with VBUS, plateau at 4.17V
    { ChargeMonitor m; uint32_t t = 0; feedRun(m, t, 4170, 0.0f, 5, 1);
      CHECK(m.state() == PowerState::Full); CHECK(m.percent() == 100); }
    // load-recovery bump then a slow light-load decline must not stay "charging" (live bug, 10 Nov)
    { ChargeMonitor m; uint32_t t = 0; feedRun(m, t, 3800, 8.0f, 4); CHECK(m.state() == PowerState::Charging);
      feedRun(m, t, 3832, -0.5f, 5); CHECK(m.state() == PowerState::OnBattery); }
    // a real charge keeps rising, so it stays Charging
    { ChargeMonitor m; uint32_t t = 0; feedRun(m, t, 3700, 4.0f, 10); CHECK(m.state() == PowerState::Charging); }
    // VBUS removed => immediately OnBattery
    { ChargeMonitor m; uint32_t t = 0; feedRun(m, t, 3700, 6.0f, 5, 1); CHECK(m.state() == PowerState::Charging);
      t += 10000; m.feed(t, 3900, 0); CHECK(m.state() == PowerState::OnBattery); }
    // screen policy: dim while charging, wakes on key, off when full
    { ChargeScreen a = chargeScreenPolicy(PowerState::Charging, 100000, 0);
      ChargeScreen b = chargeScreenPolicy(PowerState::Charging, 100000, 98000);
      CHECK(a.backlight < b.backlight); CHECK(a.refreshMs > b.refreshMs);
      ChargeScreen f = chargeScreenPolicy(PowerState::Full, 100000, 0); CHECK(!f.screenOn && f.backlight == 0); }

    // display profiles
    CHECK(findProfile("nope") == nullptr); CHECK(&profileOrDefault("garbage") == &kProfiles[0]);
    CHECK(!kProfiles[0].verified);  // we have not physically verified anything yet

    // home layout: every profile gets 4 cards, inside bounds, no overlap, big enough to read
    for (int p = 0; p < kProfileCount; p++) {
        const DisplayProfile& d = kProfiles[p]; CHECK(profileUsable(d));
        HomeLayout L = computeHome(d.w, d.h);
        for (int i = 0; i < CARD_COUNT; i++) {
            const Rect& r = L.cards[i];
            CHECK(r.x >= 0 && r.y >= L.statusBar.h && r.x + r.w <= d.w && r.y + r.h <= L.footer.y);
            CHECK(r.w >= 90 && r.h >= 40);
            for (int j = i + 1; j < CARD_COUNT; j++) {
                const Rect& q = L.cards[j];
                CHECK(r.x + r.w <= q.x || q.x + q.w <= r.x || r.y + r.h <= q.y || q.y + q.h <= r.y);
            }
        }
    }
    // firmware body area on the stock panel: y 15..121 (status 15, hints 14)
    { Rect g[CARD_COUNT]; computeGrid({0, 15, 240, 106}, g);
      for (int i = 0; i < CARD_COUNT; i++) {
        CHECK(g[i].y >= 15 && g[i].y + g[i].h <= 121 && g[i].x + g[i].w <= 240);
        CHECK(g[i].w >= 100 && g[i].h >= 40);
      } }
    CHECK(moveSelection(CARD_SUBTITLES, 'R') == CARD_ROUTINES);
    CHECK(moveSelection(CARD_ROUTINES, 'D') == CARD_MORE);
    CHECK(moveSelection(CARD_SUBTITLES, 'L') == CARD_SUBTITLES);

    // ---- talk circle Home ----
    // state machine: full happy path, barge-in, text-only reply, ESC from every state
    CHECK(talkStep(TALK_IDLE, EV_MIC_OPEN) == TALK_LISTENING);
    CHECK(talkStep(TALK_LISTENING, EV_MIC_CLOSE) == TALK_THINKING);
    CHECK(talkStep(TALK_THINKING, EV_REPLY_PLAY) == TALK_SPEAKING);
    CHECK(talkStep(TALK_SPEAKING, EV_REPLY_DONE) == TALK_IDLE);
    CHECK(talkStep(TALK_THINKING, EV_REPLY_DONE) == TALK_IDLE);
    CHECK(talkStep(TALK_SPEAKING, EV_MIC_OPEN) == TALK_LISTENING);
    CHECK(talkStep(TALK_IDLE, EV_MIC_CLOSE) == TALK_IDLE);
    CHECK(talkStep(TALK_LISTENING, EV_REPLY_PLAY) == TALK_LISTENING);
    for (int s = 0; s <= TALK_SPEAKING; s++) {
        CHECK(talkStep((TalkState)s, EV_CANCEL) == TALK_IDLE);
        CHECK(talkStep((TalkState)s, EV_FAIL) == TALK_IDLE);
    }
    CHECK(talkFromSources(false, false, false) == TALK_IDLE);
    CHECK(talkFromSources(false, false, true) == TALK_THINKING);
    CHECK(talkFromSources(false, true, true) == TALK_SPEAKING);
    CHECK(talkFromSources(true, true, true) == TALK_LISTENING);
    // animation helpers stay inside their bounds
    for (uint32_t ms = 0; ms < 20000; ms += 37) {
        const int rr = idleRingRadius(44, ms);
        CHECK(rr >= 47 && rr <= 49);
        CHECK(arcStartDeg(ms) >= 0 && arcStartDeg(ms) < 360);
    }
    CHECK(levelRingRadius(44, -5) == 47 && levelRingRadius(44, 5000) == 52 && levelRingRadius(44, 500) == 49);
    // layout: everything inside the stock 240x135 body (y 15..121), circle ~90 px, nothing overlaps
    { const TalkHomeLayout L = computeTalkHome({0, 15, 240, 106});
      CHECK(L.r * 2 >= 84 && L.r * 2 <= 92);
      CHECK(L.cx - L.r - RING_MAX >= 0 && L.cy - L.r - RING_MAX >= 15 && L.cy + L.r + RING_MAX <= 121);
      for (int i = 0; i < 3; i++) {
        const Rect& b = L.btn[i];
        CHECK(b.x >= L.cx + L.r + RING_MAX && b.x + b.w <= 240 && b.y >= 15 && b.y + b.h <= 121);
        CHECK(b.w >= 100 && b.h >= 24);
        if (i) CHECK(b.y >= L.btn[i - 1].y + L.btn[i - 1].h);
      }
      CHECK(L.sentence.y >= L.btn[2].y + L.btn[2].h && L.sentence.y + L.sentence.h <= 121);
      // pinned: preview/talk.html hard-codes these numbers
      CHECK(L.cx == 56 && L.cy == 68 && L.r == 44);
      CHECK(L.btn[0].x == 112 && L.btn[0].w == 122 && L.btn[0].h == 26);
      CHECK(L.btn[0].y == 19 && L.btn[1].y == 49 && L.btn[2].y == 79 && L.sentence.y == 109); }
    // focus walk
    CHECK(moveTalkFocus(FOCUS_TALK, 'R') == FOCUS_SAVE);
    CHECK(moveTalkFocus(FOCUS_SAVE, 'D') == FOCUS_TODAY);
    CHECK(moveTalkFocus(FOCUS_MORE, 'D') == FOCUS_MORE);
    CHECK(moveTalkFocus(FOCUS_SAVE, 'U') == FOCUS_SAVE);
    CHECK(moveTalkFocus(FOCUS_MORE, 'L') == FOCUS_TALK);
    CHECK(moveTalkFocus(FOCUS_TALK, 'D') == FOCUS_TALK);
    // theme: brand constants convert correctly
    CHECK(theme::rgb565(0xFFFFFF) == 0xFFFF && theme::rgb565(0x000000) == 0);
    CHECK(theme::rgb565(0xFF5A36) == 0xFAC6 && theme::TALK != theme::LIVE);
    CHECK(theme::TALK == 0x2B5F);  // talk-circle blue #2E6BFF
    CHECK(theme::TALK_BLUE_HI == 0x649F && theme::TALK_BLUE_DIM == 0x1ABC);
    CHECK(theme::mix(0x000000, 0xFFFFFF, 128) == 0x7F7F7F);
    // safe saves: a failed or interrupted save never loses the old file
    { FakeFs fs; fs.files["/r.tsv"] = "old";
      auto put = [&fs](const char* v) { return [&fs, v](const std::string& t) { fs.files[t] = v; return true; }; };
      CHECK(replaceFile(fs, "/r.tsv", put("new")) && fs.files["/r.tsv"] == "new" && fs.files.size() == 1);
      CHECK(!replaceFile(fs, "/r.tsv", [](const std::string&) { return false; }));
      CHECK(fs.files["/r.tsv"] == "new" && fs.files.size() == 1);
      fs.failRename = fs.renames + 1;  // old moved to .bak, then the swap fails: roll back
      CHECK(!replaceFile(fs, "/r.tsv", put("newer")) && fs.exists("/r.tsv") && fs.files["/r.tsv"] == "new");
      // restart between the renames: only .bak is left, the next read restores it
      fs.files.clear(); fs.files["/r.tsv.bak"] = "new"; fs.failRename = -1;
      recoverFile(fs, "/r.tsv");
      CHECK(fs.exists("/r.tsv") && fs.files["/r.tsv"] == "new" && !fs.exists("/r.tsv.bak"));
      CHECK(replaceFile(fs, "/fresh.tsv", put("a")) && fs.files["/fresh.tsv"] == "a"); }
    // unique note names: the same pre-clock-sync timestamp after a restart gets -1, -2
    { FakeFs fs; auto ex = [&fs](const std::string& p) { return fs.exists(p.c_str()); };
      CHECK(uniquePath("/maz/notes/19700101-000012", "txt", ex) == "/maz/notes/19700101-000012.txt");
      fs.files["/maz/notes/19700101-000012.txt"] = "a";
      CHECK(uniquePath("/maz/notes/19700101-000012", "txt", ex) == "/maz/notes/19700101-000012-1.txt");
      fs.files["/maz/notes/19700101-000012-1.txt"] = "b";
      CHECK(uniquePath("/maz/notes/19700101-000012", "txt", ex) == "/maz/notes/19700101-000012-2.txt"); }
    // alert policy: priority -> sound/LED/mood table, Quiet-aware
    { using namespace maz::alert_policy;
      CHECK(parsePriority("urgent") == Priority::Urgent && parsePriority("low") == Priority::Low);
      CHECK(parsePriority("high") == Priority::High && parsePriority("garbage") == Priority::Normal && parsePriority(nullptr) == Priority::Normal);
      for (int p = 0; p < 4; p++) {
          const Rule& r = rule(static_cast<Priority>(p));
          CHECK(r.toneCount >= 1 && r.toneCount <= MAX_TONES);
          for (int i = 0; i < r.toneCount; i++) CHECK(r.tones[i].hz >= 200 && r.tones[i].ms >= 20 && r.tones[i].ms <= 200);
      }
      // more urgent never sounds shorter or flashes less than a calmer one
      for (int p = 1; p < 4; p++) CHECK(rule(static_cast<Priority>(p)).toneCount >= rule(static_cast<Priority>(p - 1)).toneCount);
      CHECK(rule(Priority::Urgent).flashes == FLASH_UNTIL_ACK && rule(Priority::Low).flashes == 0);
      // Quiet (1) mutes low/normal only; Silent (2) leaves only urgent; Normal (0) plays all
      for (int p = 0; p < 4; p++) CHECK(shouldSound(0, static_cast<Priority>(p)));
      CHECK(!shouldSound(1, Priority::Low) && !shouldSound(1, Priority::Normal));
      CHECK(shouldSound(1, Priority::High) && shouldSound(1, Priority::Urgent));
      CHECK(!shouldSound(2, Priority::High) && shouldSound(2, Priority::Urgent));
      CHECK(forced(Priority::Urgent) && !forced(Priority::High));
      CHECK(moodFor(Priority::Low, true) == Mood::Celebrating && moodFor(Priority::Urgent, false) == Mood::NeedsAttention); }
    { using namespace maz::nod;  // The Nod: hold-to-approve timing
      CHECK(!holdDone(0) && !holdDone(HOLD_MS - 1) && holdDone(HOLD_MS) && holdDone(HOLD_MS + 5000));
      CHECK(holdPercent(0) == 0 && holdPercent(HOLD_MS / 2) == 50 && holdPercent(HOLD_MS) == 100 && holdPercent(60000) == 100);
      for (uint32_t ms = 0; ms < HOLD_MS + 50; ms += 7) CHECK(holdPercent(ms) <= holdPercent(ms + 7));  // monotonic
      CHECK(HOLD_MS >= 500 && HOLD_MS <= 1200);  // deliberate, not tedious
      CHECK(holdPercent(HOLD_MS - 1) == 99); }
    // ---- approval detail head: both Core prefixes render the amount/recipient lines ----
    { using namespace maz;
      CHECK(approvalHasDetails("ASK ON PIBLO  PAYMENT\n42.00\nto Acme Ltd"));
      CHECK(approvalHasDetails("THE NOD  PAYMENT\n42.00\nto Acme Ltd"));
      CHECK(approvalHasDetails("ASK ON PIBLO  "));      // head alone still counts as detailed
      CHECK(approvalHasDetails("THE NOD  "));
      CHECK(!approvalHasDetails("ASK ON PIBLO"));       // missing the two-space separator
      CHECK(!approvalHasDetails("THE NOD "));           // one space only
      CHECK(!approvalHasDetails("CLAUDE CODE NEEDS YOU"));
      CHECK(!approvalHasDetails("Pay Acme"));
      CHECK(!approvalHasDetails(""));
      CHECK(!approvalHasDetails("ask on piblo  PAYMENT")); }  // heads are case-sensitive
    // tone sequencer: notes start on schedule, never early, never skipped, and nothing blocks
    { ToneSeq q; ToneStep s{};
      CHECK(!q.poll(0, s) && !q.playing(0));  // empty queue is silent
      CHECK(q.push(880, 70)); CHECK(q.push(1175, 70)); CHECK(q.push(1480, 90, true, 150));
      CHECK(q.playing(0));
      CHECK(q.poll(1000, s) && s.hz == 880 && s.ms == 70 && !s.force);
      CHECK(!q.poll(1069, s));                                   // first note still owns the speaker
      CHECK(q.poll(1070, s) && s.hz == 1175);
      CHECK(!q.poll(1139, s) && q.playing(1139));
      CHECK(q.poll(1140, s) && s.hz == 1480 && s.force && s.slot == 150 && s.ms == 90);  // gap after the note
      CHECK(!q.poll(1289, s) && q.playing(1289));
      CHECK(!q.poll(1290, s) && !q.playing(1290));               // drained: silent again
      // clock wrap: the schedule survives millis() rolling over
      CHECK(q.push(500, 20)); CHECK(q.push(600, 20));
      CHECK(q.poll(0xFFFFFFF0u, s) && s.hz == 500);
      CHECK(!q.poll(0xFFFFFFFFu, s));
      CHECK(q.poll(4u, s) && s.hz == 600);                       // 0xFFFFFFF0 + 20 wraps to 4
      // overflow drops the extra notes instead of corrupting the queue; clear() cancels what is queued
      q.clear();
      for (int i = 0; i < ToneSeq::CAP; i++) CHECK(q.push(300 + i, 10));
      CHECK(!q.push(999, 10));
      CHECK(q.poll(5000, s) && s.hz == 300);
      q.clear(); CHECK(!q.poll(9000, s) && !q.playing(9000));
      // a replacement sequence starts at once even if the previous note was still sounding
      CHECK(q.push(700, 50)); CHECK(q.poll(9001, s) && s.hz == 700);
      q.clear(); CHECK(q.push(800, 50)); CHECK(q.poll(9002, s) && s.hz == 800); }

    // poll back-off: doubles per failure, capped, never faster than the base cadence, resets with fails=0
    CHECK(backoffMs(5000, 0, 15000) == 5000); CHECK(backoffMs(5000, 1, 15000) == 10000);
    CHECK(backoffMs(5000, 2, 15000) == 15000); CHECK(backoffMs(5000, 200, 15000) == 15000);
    CHECK(backoffMs(45000, 3, 30000) == 45000);  // field-mode cadence is already slower than the cap
    CHECK(backoffMs(2500, 1, 15000) == 5000);
    CHECK(backoffMs(0xC0000000u, 255, 0xFFFFFFFFu) == 0xFFFFFFFFu);  // no overflow wrap-around
    for (uint8_t f = 0; f < 20; f++) CHECK(backoffMs(5000, f, 15000) <= backoffMs(5000, f + 1, 15000));

    // ---- Easy Mode keys: only Y/ENTER approve; phone text never can ----
    { using namespace maz::easy_keys;
      CHECK(isApproveKey(KEY_Y) && isApproveKey(KEY_ENTER));
      CHECK(!isApproveKey(KEY_N) && !isApproveKey(KEY_ESC) && !isApproveKey(KEY_SPACE));
      CHECK(!isApproveKey(KEY_UP) && !isApproveKey(KEY_DOWN) && !isApproveKey(KEY_LEFT) && !isApproveKey(KEY_RIGHT));
      CHECK(!isApproveKey(KEY_BACKSPACE) && !isApproveKey(0) && !isApproveKey(0xFF));
      CHECK(isRejectKey(KEY_N) && isRejectKey(KEY_ESC) && !isRejectKey(KEY_Y) && !isRejectKey(KEY_ENTER));
      CHECK(isEasyNavKey(KEY_UP) && isEasyNavKey(KEY_DOWN) && isEasyNavKey(KEY_ENTER) && isEasyNavKey(KEY_ESC));
      CHECK(escGoesBack(false) && !escGoesBack(true));
      CHECK(backspaceGoesBack(false) && !backspaceGoesBack(true));
      CHECK(!phoneTextMayApprove()); }

    // ---- Phone text: literal insert only; never keys, never approve ----
    { using namespace maz::phone_text;
      std::string dst, clean; size_t dropped = 0, cur = 0; Mode m = Mode::Insert;
      CHECK(parseMode("insert", m) && m == Mode::Insert);
      CHECK(parseMode("replace", m) && m == Mode::Replace);
      CHECK(parseMode("send", m) && m == Mode::Send);
      CHECK(!parseMode("approve", m) && !parseMode("", m) && !parseMode(nullptr, m));
      // literal: approval words, "Y"/newlines/ESC stay plain text, never keys
      dst = "Say "; cur = dst.size();
      CHECK(applyPhoneText(dst, 120, cur, Mode::Insert, "Y approve\nENTER\x1b", true) == Status::Inserted);
      CHECK(dst == "Say Y approveENTER" && cur == dst.size());
      CHECK(dst.find('\n') == std::string::npos && dst.find('\x1b') == std::string::npos);
      // controls never become separators; non-ASCII bytes dropped
      sanitize(std::string("a\tb\xC3\xA9" "c"), clean, dropped);
      CHECK(clean == "abc" && dropped == 3);
      // ...but a message with non-ASCII is rejected whole, never silently trimmed
      dst = "keep"; cur = 4;
      CHECK(applyPhoneText(dst, 120, cur, Mode::Insert, "caf\xC3\xA9", true) == Status::Unsupported);
      CHECK(applyPhoneText(dst, 120, cur, Mode::Replace, "\xF0\x9F\x98\x80", true) == Status::Unsupported);
      CHECK(dst == "keep" && cur == 4);
      // no field focused -> NoField and untouched
      dst = "keep"; cur = 2;
      CHECK(applyPhoneText(dst, 120, cur, Mode::Insert, "hello", false) == Status::NoField);
      CHECK(dst == "keep" && cur == 2);
      // too long -> rejected whole, never partially inserted
      dst = "ab"; cur = 2;
      CHECK(applyPhoneText(dst, 4, cur, Mode::Insert, "cdef", true) == Status::TooLong);
      CHECK(dst == "ab" && cur == 2);
      CHECK(applyPhoneText(dst, 4, cur, Mode::Replace, "abcdef", true) == Status::TooLong);
      CHECK(dst == "ab" && cur == 2);
      // insert at cursor; cursor follows the insert
      dst = "ac"; cur = 1;
      CHECK(applyPhoneText(dst, 120, cur, Mode::Insert, "b", true) == Status::Inserted);
      CHECK(dst == "abc" && cur == 2);
      // replace swaps the whole buffer and moves the cursor to the end
      dst = "old"; cur = 1;
      CHECK(applyPhoneText(dst, 120, cur, Mode::Replace, "new", true) == Status::Inserted);
      CHECK(dst == "new" && cur == 3);
      // send inserts only: no trailing ENTER/separator is ever synthesized
      dst.clear(); cur = 0;
      CHECK(applyPhoneText(dst, 120, cur, Mode::Send, "send it", true) == Status::Inserted);
      CHECK(dst == "send it");
      // cursor clamps to the end, never past it
      dst = "ab"; cur = 99;
      CHECK(applyPhoneText(dst, 120, cur, Mode::Insert, "c", true) == Status::Inserted);
      CHECK(dst == "abc" && cur == 3);
      // empty text never inserts (Core validates 1..500; firmware is strict too)
      dst = "ab"; cur = 0;
      CHECK(applyPhoneText(dst, 120, cur, Mode::Insert, "\n\x1b", true) == Status::Inserted);
      CHECK(dst == "ab" && cur == 0);
      // freshness: 2 min TTL, inclusive at the boundary; unsafe clock -> fresh
      CHECK(fresh(1000, 1120, true) && !fresh(1000, 1121, true));
      CHECK(fresh(1000, 999999, false) && !fresh(0, 2000, true));
      // ids: bounded printable; dedupe ring never holds a repeat
      CHECK(validId("a-1") && !validId("") && !validId(nullptr));
      char longId[80]; memset(longId, 'x', sizeof(longId) - 1); longId[sizeof(longId) - 1] = 0;
      CHECK(!validId(longId));
      SeenIds seen;
      CHECK(!seen.seen("id-1")); seen.add("id-1"); CHECK(seen.seen("id-1"));
      seen.add("id-1"); CHECK(seen.count == 1);  // re-adding a repeat is a no-op
      for (int i = 2; i <= 10; ++i) { char id[16]; snprintf(id, sizeof(id), "id-%d", i); seen.add(id); }
      CHECK(seen.count == kMaxSeenIds && !seen.seen("id-1") && seen.seen("id-10")); }

    // pair_connect.h: discovery reply, code format, device id, poll states
    { using namespace nod::pair;
      Discovery d;
      CHECK(parseDiscovery("{\"piblo\":1,\"port\":8787,\"name\":\"MAZPC\",\"version\":\"1.2\"}", d));
      CHECK(d.port == 8787 && d.name == "MAZPC");
      CHECK(parseDiscovery("{ \"piblo\" : 1 , \"port\" : 9000 }", d) && d.port == 9000 && d.name.empty());
      CHECK(!parseDiscovery("{\"piblo\":2,\"port\":8787}", d));
      CHECK(!parseDiscovery("{\"port\":8787}", d));
      CHECK(!parseDiscovery("{\"piblo\":1,\"port\":0}", d));
      CHECK(!parseDiscovery("{\"piblo\":1,\"port\":70000}", d));
      CHECK(!parseDiscovery("not json", d));
      CHECK(printableName(std::string("ab\x01\x7f" "c"), 32) == "abc");
      CHECK(printableName(std::string(50, 'x'), 32).size() == 32);
      CHECK(formatCode("482913") == "482 913");
      CHECK(formatCode("48291").empty() && formatCode("4829133").empty() && formatCode("48a913").empty());
      const uint8_t mac[6] = {0x12, 0x34, 0x56, 0xAB, 0xCD, 0x0F};
      CHECK(deviceIdFromMac(mac) == "piblo-abcd0f");
      CHECK(requestBody("piblo-abcd0f", "Piblo") == "{\"device_id\":\"piblo-abcd0f\",\"name\":\"Piblo\"}");
      std::string tok;
      CHECK(jsonString("{\"status\": \"approved\", \"token\": \"t\\\"k\"}", "token", tok) && tok == "t\"k");
      CHECK(nextPhase(Phase::Waiting, 200, "pending") == Phase::Waiting);
      CHECK(nextPhase(Phase::Waiting, 200, "approved") == Phase::Approved);
      CHECK(nextPhase(Phase::Waiting, 200, "denied") == Phase::Denied);
      CHECK(nextPhase(Phase::Waiting, 200, "expired") == Phase::TimedOut);
      CHECK(nextPhase(Phase::Waiting, 404, "") == Phase::TimedOut);
      CHECK(nextPhase(Phase::Waiting, -1, "") == Phase::Waiting);
      CHECK(nextPhase(Phase::Waiting, 500, "") == Phase::Waiting);
      CHECK(nextPhase(Phase::Denied, 200, "approved") == Phase::Denied);
      CHECK(afterElapsed(Phase::Waiting, 119999) == Phase::Waiting);
      CHECK(afterElapsed(Phase::Waiting, 120000) == Phase::TimedOut);
      CHECK(afterElapsed(Phase::Approved, 999999) == Phase::Approved); }

    printf("%d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
