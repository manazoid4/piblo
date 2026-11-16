// Piblo board offload protocol over ESP-NOW (Cardputer <-> Piblo board). Pure C++11, no Arduino:
// the SAME file is copied into the Cardputer firmware (src/net/) and host tests.
//
// All frames are unicast ESP-NOW (AES-CCMP, per-peer key derived from the Core token), except the
// two discovery frames which are broadcast and carry an HMAC tag instead.
//   B0 02 ver_major ver_minor ver_patch tag[8]     BEACON  board -> *      tag = HMAC(k,"B"+boardMac)
//   B0 01 tag[8]                                   HELLO   cardputer -> *  tag = HMAC(k,"C"+cardMac)
//   B1 sid kind total32 crc32                      BEGIN   (kind 0 = /dictate pc-paste, 1 = /transcribe/raw)
//   B2 sid idx16 data[<=240]                       DATA    chunk idx = byte offset / 240
//   B3 sid next16 status                           ACK     cumulative: next chunk expected; status 0 ok, 1 bad, 2 busy
//   B7 sid                                         QUERY   cardputer -> board
//   B5 sid code http16 text...                     RESULT  code 0 ok,1 no wifi,2 no cfg,3 bad upload,4 http err,
//                                                          5 busy, 0xFF still working
//   B8 / B9 status                                 PING / PONG(status bit0 = ready: core configured + PSRAM buffer)
// Integrity: ESP-NOW CRC + CCMP MIC per frame, cumulative ACK + go-back-N for loss, CRC-32 over the whole upload.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace pbp {
constexpr uint8_t T_DISC = 0xB0, T_BEGIN = 0xB1, T_DATA = 0xB2, T_ACK = 0xB3, T_RESULT = 0xB5,
                  T_QUERY = 0xB7, T_PING = 0xB8, T_PONG = 0xB9;
constexpr uint8_t D_HELLO = 1, D_BEACON = 2;
constexpr size_t  CHUNK = 240;                 // + 4 header = 244 <= 250 ESP-NOW max
constexpr size_t  MAX_UPLOAD = 1024 * 1024;
constexpr uint8_t RES_WORKING = 0xFF;

inline uint32_t crc32(const uint8_t* d, size_t n, uint32_t c = 0) {
    c = ~c;
    while (n--) { c ^= *d++; for (int i = 0; i < 8; i++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u))); }
    return ~c;
}
inline void put32(uint8_t* p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
inline uint32_t get32(const uint8_t* p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }
}  // namespace pbp