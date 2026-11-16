#pragma once
#include <string>

namespace maz {

// A detailed approval carries its own three-line layout (head / amount / recipient) in the
// single `summary` field the firmware already polls, so no new wire format is needed.
// Core sends the "ASK ON PIBLO  " head; builds from before the rename sent "THE NOD  ".
// Both heads must render the detail lines, so accept either prefix.
inline bool approvalHasDetails(const std::string& summary) {
    return summary.compare(0, 14, "ASK ON PIBLO  ") == 0 ||
           summary.compare(0, 9, "THE NOD  ") == 0;
}

}  // namespace maz
