// Piblo Link: framed UART protocol between the Cardputer and a coprocessor board.
// Shared by both firmwares; pure C++11 (no Arduino) so it runs in the host tests.
//
// Frame:  SOF(0xA5) | ver | type | seq | len_lo len_hi | payload[len] | crc16 (lo hi)
//         crc16 = CRC-16/CCITT-FALSE over ver..payload. len <= MAX_PAYLOAD.
// Types:  HELLO 1 (payload: rxSlots, isReply), HEARTBEAT 2 (payload: free rx slots), CREDIT 3 (payload: n),
//         0x10.. = application DATA (the link only carries them).
// Flow control: credits. A side may send DATA only while it holds credit; the peer
//         grants credit as its app drains the bounded RX queue. A DATA frame lost on the wire would
//         leak its credit forever, so HEARTBEAT carries the peer's free RX slots; a sender stuck at
//         zero credit with nothing in flight (>= 2 heartbeats since its last DATA) resyncs to it.
// Reconnect: a silent peer (> TIMEOUT_MS) drops the link to Down; HELLO is sent every
//         HELLO_MS until the peer answers. A HELLO from a rebooted peer resets seq/credit.
// Fallback: the app calls useCoprocessor(); false (Down / Incompatible) means "use your own
//         Wi-Fi", and it flips back on its own after a fresh handshake.
// No I/O here: feed bytes with onByte(), drain wire bytes with pullTx(), call tick(now).
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace nod {
namespace link {

constexpr uint8_t  SOF = 0xA5;
constexpr uint8_t  VERSION = 1;
constexpr size_t   MAX_PAYLOAD = 240;
constexpr size_t   MAX_FRAME = 1 + 1 + 1 + 1 + 2 + MAX_PAYLOAD + 2;
constexpr size_t   RX_SLOTS = 4;        // DATA frames buffered for the app
constexpr size_t   TX_BYTES = 640;      // bounded wire queue (2 max frames + control)
constexpr uint32_t HEARTBEAT_MS = 250;
constexpr uint32_t TIMEOUT_MS = 1000;
constexpr uint32_t HELLO_MS = 500;

enum Type : uint8_t { HELLO = 1, HEARTBEAT = 2, CREDIT = 3, DATA_MIN = 0x10 };
enum State : uint8_t { Down, Up, Incompatible };

inline uint16_t crc16(const uint8_t* d, size_t n, uint16_t crc = 0xFFFF) {
    while (n--) {
        crc ^= (uint16_t)((uint16_t)(*d++) << 8);
        for (int i = 0; i < 8; i++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

struct Frame {
    uint8_t  type = 0, seq = 0;
    uint16_t len = 0;
    uint8_t  payload[MAX_PAYLOAD] = {0};
};

// Returns bytes written, 0 if payload too big or out too small.
inline size_t encode(uint8_t type, uint8_t seq, const uint8_t* p, size_t len, uint8_t* out, size_t cap) {
    if (len > MAX_PAYLOAD || cap < len + 8) return 0;
    out[0] = SOF; out[1] = VERSION; out[2] = type; out[3] = seq;
    out[4] = (uint8_t)(len & 0xFF); out[5] = (uint8_t)(len >> 8);
    if (len) memcpy(out + 6, p, len);
    const uint16_t c = crc16(out + 1, 5 + len);
    out[6 + len] = (uint8_t)(c & 0xFF); out[7 + len] = (uint8_t)(c >> 8);
    return len + 8;
}

// Byte-at-a-time parser. Any error drops the frame and hunts for the next SOF.
class Parser {
public:
    uint32_t crcErrors = 0, versionMismatch = 0, badLength = 0, resyncBytes = 0;

    bool feed(uint8_t b, Frame& out) {
        if (st_ == Hunt) {
            if (b == SOF) { st_ = Header; n_ = 0; } else resyncBytes++;
            return false;
        }
        if (st_ == Header) {
            h_[n_++] = b;  // ver type seq lenLo lenHi
            if (n_ < 5) return false;
            if (h_[0] != VERSION) { versionMismatch++; st_ = Hunt; return false; }
            len_ = (uint16_t)(h_[3] | (h_[4] << 8));
            if (len_ > MAX_PAYLOAD) { badLength++; st_ = Hunt; return false; }
            n_ = 0;
            st_ = len_ ? Payload : Crc;
            return false;
        }
        if (st_ == Payload) {
            cur_.payload[n_++] = b;
            if (n_ == len_) { n_ = 0; st_ = Crc; }
            return false;
        }
        c_[n_++] = b;  // Crc
        if (n_ < 2) return false;
        st_ = Hunt;
        const uint16_t want = crc16(cur_.payload, len_, crc16(h_, 5));
        if (want != (uint16_t)(c_[0] | (c_[1] << 8))) { crcErrors++; return false; }
        cur_.type = h_[1]; cur_.seq = h_[2]; cur_.len = len_;
        out = cur_;
        return true;
    }
    void reset() { st_ = Hunt; }

private:
    enum S { Hunt, Header, Payload, Crc } st_ = Hunt;
    Frame cur_;  // payload assembled here: `out` may be a fresh local on every call
    uint8_t h_[5] = {0}; uint8_t c_[2] = {0}; uint16_t len_ = 0; size_t n_ = 0;
};

class Link {
public:
    // counters for the benchmark / health lines
    uint32_t seqGaps = 0, rxOverflow = 0, txOverflow = 0, fallbacks = 0, reconnects = 0, noCredit = 0, creditResyncs = 0;
    Parser   parser;

    State   state() const { return state_; }
    bool    useCoprocessor() const { return state_ == Up; }
    uint8_t credits() const { return credits_; }

    // --- wire in ---------------------------------------------------------------------
    void onByte(uint8_t b, uint32_t now) {
        const uint32_t vm = parser.versionMismatch;
        Frame f;
        const bool got = parser.feed(b, f);
        if (parser.versionMismatch != vm) markIncompatible();
        if (got) onFrame(f, now);
    }

    // --- app out ---------------------------------------------------------------------
    // DATA only while Up and holding credit. False = try later (or use the fallback).
    bool send(uint8_t type, const uint8_t* p, size_t len) {
        if (type < DATA_MIN || state_ != Up) return false;
        if (credits_ == 0) { noCredit++; return false; }
        if (!queue(type, p, len)) return false;
        credits_--; lastDataTx_ = lastTick_;
        return true;
    }
    // --- app in ----------------------------------------------------------------------
    bool recv(Frame& out) {
        if (rxCount_ == 0) return false;
        out = rx_[rxHead_];
        rxHead_ = (rxHead_ + 1) % RX_SLOTS; rxCount_--;
        owedCredit_++;  // tell the peer it may send one more
        return true;
    }

    // --- timing + wire out -----------------------------------------------------------
    void tick(uint32_t now) {
        lastTick_ = now;
        if (state_ == Up && (uint32_t)(now - lastRx_) > TIMEOUT_MS) {
            state_ = Down; credits_ = 0; fallbacks++;
        }
        if (state_ == Up) {
            if (owedCredit_) { const uint8_t n = owedCredit_; if (queue(CREDIT, &n, 1)) owedCredit_ = 0; }
            const uint8_t freeSlots = (uint8_t)(RX_SLOTS - rxCount_);
            if ((uint32_t)(now - lastTx_) >= HEARTBEAT_MS && queue(HEARTBEAT, &freeSlots, 1)) lastTx_ = now;
        } else if (state_ == Down && (uint32_t)(now - lastTx_) >= HELLO_MS) {
            sendHello(false);
            lastTx_ = now;
        }
    }
    size_t pullTx(uint8_t* out, size_t cap) {
        size_t n = 0;
        while (n < cap && txCount_) { out[n++] = tx_[txHead_]; txHead_ = (txHead_ + 1) % TX_BYTES; txCount_--; }
        return n;
    }
    size_t txPending() const { return txCount_; }

private:
    State    state_ = Down;
    uint8_t  credits_ = 0, owedCredit_ = 0, txSeq_ = 0, expectSeq_ = 0;
    bool     haveSeq_ = false;
    uint32_t lastRx_ = 0, lastTx_ = 0, lastTick_ = 0, lastDataTx_ = 0;
    Frame    rx_[RX_SLOTS];
    size_t   rxHead_ = 0, rxCount_ = 0;
    uint8_t  tx_[TX_BYTES] = {0};
    size_t   txHead_ = 0, txCount_ = 0;

    void markIncompatible() {
        if (state_ == Up) fallbacks++;
        state_ = Incompatible; credits_ = 0;
    }
    bool queue(uint8_t type, const uint8_t* p, size_t len) {
        uint8_t buf[MAX_FRAME];
        const size_t n = encode(type, txSeq_, p, len, buf, sizeof buf);
        if (!n || TX_BYTES - txCount_ < n) { txOverflow++; return false; }
        for (size_t i = 0; i < n; i++) tx_[(txHead_ + txCount_ + i) % TX_BYTES] = buf[i];
        txCount_ += n; txSeq_++;
        return true;
    }
    void sendHello(bool isReply) {
        const uint8_t pl[2] = {(uint8_t)RX_SLOTS, (uint8_t)(isReply ? 1 : 0)};
        queue(HELLO, pl, 2);
    }
    void onFrame(const Frame& f, uint32_t now) {
        lastRx_ = now;
        if (state_ == Incompatible) state_ = Down;  // a valid current-version frame: try again
        if (f.type == HELLO && f.len >= 2) {
            if (state_ != Up) reconnects++;
            state_ = Up; credits_ = f.payload[0]; owedCredit_ = 0;
            haveSeq_ = true; expectSeq_ = (uint8_t)(f.seq + 1);  // peer (re)started: no gap
            rxCount_ = rxHead_ = 0;
            if (f.payload[1] == 0) { sendHello(true); lastTx_ = now; }
            return;
        }
        if (state_ != Up) return;  // everything else needs a handshake first
        if (haveSeq_ && f.seq != expectSeq_) seqGaps += (uint8_t)(f.seq - expectSeq_);
        haveSeq_ = true; expectSeq_ = (uint8_t)(f.seq + 1);
        if (f.type == CREDIT && f.len >= 1) {
            const unsigned sum = credits_ + f.payload[0];
            credits_ = sum > RX_SLOTS ? (uint8_t)RX_SLOTS : (uint8_t)sum;  // never above the peer's slots
        } else if (f.type == HEARTBEAT && f.len >= 1) {
            // lost-DATA credit resync: only when nothing can still be in flight
            if (credits_ == 0 && (uint32_t)(now - lastDataTx_) >= 2 * HEARTBEAT_MS && f.payload[0] <= RX_SLOTS) {
                credits_ = f.payload[0]; creditResyncs++;
            }
        } else if (f.type >= DATA_MIN) {
            if (rxCount_ == RX_SLOTS) { rxOverflow++; return; }  // peer ignored its credit
            rx_[(rxHead_ + rxCount_) % RX_SLOTS] = f; rxCount_++;
        }
    }
};

}  // namespace link
}  // namespace nod
