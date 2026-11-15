// One background upload at a time, shared by the UI thread and a worker task.
// Pure C++ (no Arduino/FreeRTOS) so the state machine runs in the host tests.
//
//   Idle --begin--> Running --worker done--> Done --take--> Idle
//                      |  cancel / timeout
//                      v
//                  Abandoned --worker done--> Idle   (result thrown away)
//
// Abandoned still counts as busy: the HTTP call cannot be aborted, so a new
// upload is refused until the old one really ends. That is what stops a
// second Flow paste being sent while the first may still be typing.
#pragma once

#include <stdint.h>

#include <atomic>

namespace nod {

class UploadGate {
public:
    enum State : uint8_t { Idle, Running, Done, Abandoned };

    // UI thread. False if any upload (even an abandoned one) is still alive.
    bool begin(uint32_t nowMs) {
        uint8_t want = Idle;
        if (!s_.compare_exchange_strong(want, Running)) return false;
        startedAt_ = nowMs;
        return true;
    }
    // UI thread: the worker task could not be created.
    void abort() {
        uint8_t want = Running;
        s_.compare_exchange_strong(want, Idle);
    }
    // Worker thread, once, after the result is written.
    void finish() {
        uint8_t want = Running;
        if (s_.compare_exchange_strong(want, Done)) return;
        want = Abandoned;
        s_.compare_exchange_strong(want, Idle);
    }
    // UI thread: result ready? Frees the gate for the next upload.
    bool take() {
        uint8_t want = Done;
        return s_.compare_exchange_strong(want, Idle);
    }
    // UI thread: stop caring about a running upload. False if the worker
    // already finished (state is Done: take() the result or discard it).
    bool abandon() {
        uint8_t want = Running;
        return s_.compare_exchange_strong(want, Abandoned);
    }
    bool timedOut(uint32_t nowMs, uint32_t limitMs) const {
        return s_.load() == Running && (uint32_t)(nowMs - startedAt_) >= limitMs;
    }
    State state() const { return (State)s_.load(); }
    bool busy() const { return s_.load() != Idle; }

private:
    std::atomic<uint8_t> s_{Idle};
    uint32_t startedAt_ = 0;
};

}  // namespace nod
