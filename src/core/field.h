#pragma once

#include <stdint.h>
#include <string>

#include "../net/mazhost.h"

namespace maz {
namespace field {

void begin();
void update();

void armContext(const std::string& appId, const std::string& snapshot);
const std::string& context();
bool contextArmed();
void clearContext();

std::string nowText();

const char* quickId(int slot);
const char* quickLabel(int slot);
bool runQuick(int slot);
void cycleQuick(int slot);

void toggleFieldMode();
void toggleShift();
void toggleHalt(); // Fn+H: immediately pause or resume new nod-mediated asks
std::string shiftElapsedText();

// Re-read Core firmware info now (worker permitting); confirmAfter opens the install confirm when done.
void checkFirmwareNow(bool confirmAfter);

void requestSystemStatus();
void requestWorkSummary();
void requestToday();             // refresh the Today screen data
const host::TodayStatus& today();  // last result (ok=false until one arrives)
bool requestWorkIncrement(const std::string& trackId, const std::string& eventTypeId);
void queueBeam(const std::string& text);

}  // namespace field
}  // namespace maz
