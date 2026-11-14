// Piblo - firmware version guard for over-the-air updates.
//
// The device installs the image Core has staged. A stale or older staged image must never downgrade it,
// so an update goes ahead only when the staged version is strictly newer. Unparseable versions fail
// closed. Accepts "1.6.0", "v1.6.0" and "1.6.0-beta" (suffix ignored). Pure logic for PC tests.
#pragma once
#include <stdio.h>

namespace nod {

inline bool parseFwVersion(const char* s, int out[3]) {
    if (!s) return false;
    if (*s == 'v' || *s == 'V') ++s;
    out[0] = out[1] = out[2] = 0;
    const int n = sscanf(s, "%d.%d.%d", &out[0], &out[1], &out[2]);
    return n >= 2 && out[0] >= 0 && out[1] >= 0 && out[2] >= 0;
}

inline bool fwIsNewer(const char* staged, const char* running) {
    int a[3], b[3];
    if (!parseFwVersion(staged, a) || !parseFwVersion(running, b)) return false;
    for (int i = 0; i < 3; ++i) if (a[i] != b[i]) return a[i] > b[i];
    return false;  // equal: nothing to install
}

}  // namespace nod
