// Piblo Cardputer - ESP-NOW link to the Piblo board (offload service).
#pragma once
#include <stdint.h>
#include <string>
#include "piblo_board_proto.h"

namespace maz {
namespace board {

// Current link state.
struct State {
    bool linked = false;          // peer discovered + keyed
    bool healthy = false;         // PONG received with ready=1 within last 6 s
    uint32_t lastPongMs = 0;      // millis() of last PONG
    uint8_t boardMac[6] = {0};
    bool haveMac = false;
};

// Initialize the board link. Call once from setup().
void begin();

// Call every loop iteration.
void update();

// Get current state (read-only).
const State& state();

// Returns true if the board is ready to accept an upload.
inline bool canUpload() { return state().healthy; }

// Upload a WAV file to the board (offload).
// kind: 0 = /dictate?target=pc-paste, 1 = /transcribe/raw
// Returns empty string on failure, otherwise the transcript / PC outcome text.
std::string upload(uint8_t kind, const std::string& wavPath, uint32_t timeoutMs = 30000);

// Ping the board to refresh health.
void ping();

// Force re-discovery (e.g. after Core config change).
void rediscover();

// Returns the board MAC as a string (e.g., "aa:bb:cc:dd:ee:ff").
std::string macString();

// One-line link diagnostic for the System screen, e.g. "radio up, key ok, 12 hello".
std::string diag();

}  // namespace board
}  // namespace maz