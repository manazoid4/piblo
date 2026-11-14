// Host test: g++ -std=c++17 -I<ArduinoJson>/src -I.. test/test_desk_state.cpp && ./a.out
#include "../desk_state.h"
#include <cassert>
#include <cstdio>
int main() {
  DeskState s;
  const char* j = "{\"ts\":5,\"extra\":{\"x\":1},\"buddy\":{\"state\":\"thinking\",\"mood\":\"focused\",\"line\":\"caf\xc3\xa9 time\"},"
    "\"task\":{\"title\":\"Ship the Piblo desk firmware today please\",\"project\":\"piblo\"},"
    "\"focus\":{\"active\":true,\"label\":\"Deep\",\"remaining_s\":754,\"total_s\":1500},"
    "\"agents\":{\"working\":2,\"waiting\":1,\"stale\":0,\"latest\":\"pr #51 green\"},\"approvals\":{\"pending\":3,\"top\":\"merge\"},"
    "\"flow\":{\"last_text\":\"hello\",\"last_pc\":\"pasted\"},\"core\":{\"ok\":true,\"version\":\"1.6\"},"
    "\"device\":{\"battery\":null,\"charging\":null,\"app\":\"desk\"}}";
  assert(deskParse(j, strlen(j), s) && s.valid);
  assert(!strcmp(s.bState, "thinking") && s.working == 2 && s.focusRemain == 754 && s.approvalsPending == 3);
  assert(s.battery == -1 && s.charging == -1 && s.coreOk);
  assert(strchr(s.bLine, '?'));                       // non-ASCII neutralised
  const char* nul = "{\"buddy\":{\"state\":\"idle\"},\"task\":null,\"focus\":null,\"approvals\":null,\"device\":null,\"core\":{\"ok\":false}}";
  assert(deskParse(nul, strlen(nul), s) && !s.hasTask && !s.hasFocus && !s.hasApprovals && !s.coreOk);
  assert(!deskParse("{}", 2, s) && !s.valid);
  assert(!deskParse("not json", 8, s) && !s.valid);
  assert(!deskParse("[1,2]", 5, s));
  char o[16]; deskClip(o, sizeof o, "abcdefghijklmnopqrstuvwxyz", 10); assert(!strcmp(o, "abcdefg..."));
  deskClip(o, sizeof o, "short", 10); assert(!strcmp(o, "short"));
  deskClip(o, 6, "abcdefghij", 40); assert(strlen(o) <= 5);   // never overflows cap
  char m[8]; deskMmss(m, sizeof m, 754); assert(!strcmp(m, "12:34")); deskMmss(m, sizeof m, -4); assert(!strcmp(m, "00:00"));
  FlipDetector f; for (int i = 0; i < 4; i++) f.feed(-1.0f); assert(!f.down);  // debounce
  f.feed(-1.0f); assert(f.down);
  f.feed(-0.5f); f.feed(-0.5f); for (int i = 0; i < 9; i++) f.feed(-0.5f); assert(f.down);  // hysteresis band holds
  for (int i = 0; i < 5; i++) f.feed(0.9f); assert(!f.down);
  puts("desk_state tests OK"); return 0;
}
