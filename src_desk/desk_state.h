// Pure, Arduino-free logic for Piblo Desk Mode: /desk/state parsing, text clipping, mm:ss, flip detector.
// Host-testable: see test/test_desk_state.cpp. Needs ArduinoJson (header-only).
#pragma once
#include <ArduinoJson.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

struct DeskState {
  bool    valid = false;
  int32_t ts = 0;
  char    bState[12] = "idle";   // idle|listening|thinking|speaking|alert|asleep
  char    bMood[10]  = "calm";   // calm|happy|focused|worried
  char    bLine[44]  = "";
  bool    hasTask = false;
  char    taskTitle[44] = "";
  char    taskProject[24] = "";
  bool    hasFocus = false;
  bool    focusActive = false;
  char    focusLabel[28] = "";
  int32_t focusRemain = 0, focusTotal = 0;
  int32_t working = 0, waiting = 0, stale = 0;
  char    latest[44] = "";
  bool    hasApprovals = false;
  int32_t approvalsPending = 0;
  char    approvalsTop[44] = "";
  char    flowText[64] = "";
  char    flowPc[12] = "";       // pasted|clipboard|failed|""
  bool    coreOk = false;
  char    coreVer[12] = "";
  int32_t battery = -1;          // -1 = unknown
  int8_t  charging = -1;         // -1 unknown, 0/1
  char    app[20] = "";
};

// Copy to a bounded buffer, printable ASCII only ('?' for anything else), never overflows.
inline void deskCopy(char* dst, size_t cap, const char* src) {
  size_t i = 0;
  if (src) for (; src[i] && i + 1 < cap; i++) {
    unsigned char c = (unsigned char)src[i];
    dst[i] = (c >= 32 && c < 127) ? (char)c : '?';
  }
  dst[i] = 0;
}

// Truncate to maxChars with a trailing "..." (3 chars) when it does not fit.
inline void deskClip(char* out, size_t cap, const char* in, size_t maxChars) {
  if (cap == 0) return;
  if (maxChars + 1 > cap) maxChars = cap - 1;
  size_t n = strlen(in);
  if (n <= maxChars) { memcpy(out, in, n); out[n] = 0; return; }
  if (maxChars < 4) { memcpy(out, in, maxChars); out[maxChars] = 0; return; }
  memcpy(out, in, maxChars - 3); memcpy(out + maxChars - 3, "...", 3); out[maxChars] = 0;
}

inline void deskMmss(char* out, size_t cap, int32_t s) {
  if (s < 0) s = 0;
  if (s > 99 * 60 + 59) s = 99 * 60 + 59;
  snprintf(out, cap, "%02d:%02d", (int)(s / 60), (int)(s % 60));
}

// Returns false (and leaves out.valid=false) on bad JSON / not an object. Unknown fields are ignored.
inline bool deskParse(const char* json, size_t len, DeskState& o) {
  o.valid = false;
  JsonDocument doc;  // ArduinoJson 7: heap-grown; bounded by the caller's body cap + nesting limit
  if (deserializeJson(doc, json, len, DeserializationOption::NestingLimit(6)) || !doc.is<JsonObject>()) return false;
  if (!doc["buddy"]["state"].is<const char*>() || !doc["core"]["ok"].is<bool>()) return false;
  DeskState s;
  s.ts = doc["ts"] | 0;
  JsonObjectConst b = doc["buddy"];
  deskCopy(s.bState, sizeof s.bState, b["state"] | "idle");
  deskCopy(s.bMood, sizeof s.bMood, b["mood"] | "calm");
  deskCopy(s.bLine, sizeof s.bLine, b["line"] | "");
  JsonObjectConst t = doc["task"];
  if (!t.isNull()) { s.hasTask = true; deskCopy(s.taskTitle, sizeof s.taskTitle, t["title"] | ""); deskCopy(s.taskProject, sizeof s.taskProject, t["project"] | ""); }
  JsonObjectConst f = doc["focus"];
  if (!f.isNull()) {
    s.hasFocus = true; s.focusActive = f["active"] | false;
    deskCopy(s.focusLabel, sizeof s.focusLabel, f["label"] | "");
    s.focusRemain = f["remaining_s"] | 0; s.focusTotal = f["total_s"] | 0;
  }
  JsonObjectConst a = doc["agents"];
  s.working = a["working"] | 0; s.waiting = a["waiting"] | 0; s.stale = a["stale"] | 0;
  deskCopy(s.latest, sizeof s.latest, a["latest"] | "");
  JsonObjectConst ap = doc["approvals"];
  if (!ap.isNull()) { s.hasApprovals = true; s.approvalsPending = ap["pending"] | 0; deskCopy(s.approvalsTop, sizeof s.approvalsTop, ap["top"] | ""); }
  JsonObjectConst fl = doc["flow"];
  deskCopy(s.flowText, sizeof s.flowText, fl["last_text"] | "");
  deskCopy(s.flowPc, sizeof s.flowPc, fl["last_pc"] | "");
  JsonObjectConst c = doc["core"];
  s.coreOk = c["ok"] | false; deskCopy(s.coreVer, sizeof s.coreVer, c["version"] | "");
  JsonObjectConst d = doc["device"];
  if (!d.isNull()) {
    if (d["battery"].is<int>()) s.battery = d["battery"].as<int>();
    if (d["charging"].is<bool>()) s.charging = d["charging"].as<bool>() ? 1 : 0;
    deskCopy(s.app, sizeof s.app, d["app"] | "");
  }
  s.valid = true;
  o = s;
  return true;
}

// Face-down detector on accel Z (in g) with hysteresis + debounce (N consecutive samples).
// TODO-VERIFY on hardware: which sign of Z is "screen down" depends on QMI8658 mounting.
struct FlipDetector {
  bool down = false;
  uint8_t cnt = 0;
  float enterBelow = -0.70f, exitAbove = -0.30f;  // down when az < -0.70 ; up again when az > -0.30
  uint8_t need = 5;
  bool feed(float az) {
    bool want = down ? (az < exitAbove) : (az < enterBelow);
    if (want == down) { cnt = 0; return down; }  // no change requested
    if (++cnt >= need) { down = !down; cnt = 0; }
    return down;
  }
};
