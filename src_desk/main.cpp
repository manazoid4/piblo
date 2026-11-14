#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <pair_connect.h>
#include "desk_state.h"

// Waveshare ESP32-S3-LCD-1.47B: LCD 45/40/42/41/39/46, 172x320 ST7789.
Arduino_ESP32SPI bus(41, 42, 40, 45, GFX_NOT_DEFINED);
Arduino_ST7789 display(&bus, 39, 0, true, 172, 320, 34, 0, 34, 0);
Preferences prefs;
String host, token, ssid, password, input;
uint16_t port = 0;
uint32_t nextTry = 0;
bool pairingAttempted = false;
bool inputOverflow = false;
bool storageReady = false;

void show(const char* heading, const char* detail = "") {
  display.fillScreen(BLACK);
  display.setTextColor(WHITE);
  display.setTextSize(2);
  display.setCursor(6, 12);
  display.println("Piblo");
  display.setTextSize(1);
  display.setCursor(6, 48);
  display.println(heading);
  display.setCursor(6, 70);
  display.println(detail);
  Serial.printf("desk: %s\n", heading);
}

// Reject unknown-length or oversized Core responses.
int request(const String& path, const char* method, const String& body, String& response, bool auth = false) {
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(1500);
  http.setTimeout(2500);
  if (!http.begin(client, "http://" + host + ":" + String(port) + path)) return -1;
  if (auth) http.addHeader("Authorization", "Bearer " + token);
  if (body.length()) http.addHeader("Content-Type", "application/json");
  int status = body.length() ? http.sendRequest(method, body) : http.sendRequest(method);
  response = "";
  if (status > 0) {
    if (http.getSize() < 0 || http.getSize() > 2048) status = -1;
    else {
      WiFiClient* stream = http.getStreamPtr();
      uint32_t start = millis();
      while (http.connected() && (http.getSize() < 0 || response.length() < (size_t)http.getSize())) {
        while (stream->available()) {
          if (response.length() >= 2048) { status = -1; break; }
          response += (char)stream->read();
          start = millis();
        }
        if (status < 0 || millis() - start > 2500) break;
        delay(1);
      }
      if (response.length() != (size_t)http.getSize()) status = -1;
    }
  }
  http.end();
  return status;
}

bool discover() {
  WiFiUDP udp;
  if (!udp.begin(0)) return false;
  for (int attempt = 0; attempt < 3; ++attempt) {
    IPAddress targets[] = {IPAddress(255,255,255,255), WiFi.broadcastIP()};
    for (int i = 0; i < 2; ++i) {
      if (i && targets[1] == targets[0]) break;
      udp.beginPacket(targets[i], 8788);
      udp.print("PIBLO-DISCOVER 1\n");
      udp.endPacket();
    }
    uint32_t start = millis();
    while (millis() - start < 400) {
      int n = udp.parsePacket();
      if (n > 0 && n < 400) {
        char data[400];
        int got = udp.read((uint8_t*)data, sizeof(data) - 1);
        if (got > 0) {
          data[got] = 0;
          nod::pair::Discovery found;
          if (nod::pair::parseDiscovery(data, found)) {
            host = udp.remoteIP().toString(); port = found.port; udp.stop(); return true;
          }
        }
      } else if (n > 0) udp.flush();
      delay(20);
    }
  }
  udp.stop();
  return false;
}

bool pairCore() {
  show("Finding computer");
  if (!discover()) { show("Computer not found", "Open Piblo on your PC"); return false; }
  uint8_t mac[6]; WiFi.macAddress(mac);
  String response;
  const std::string payload = nod::pair::requestBody(nod::pair::deviceIdFromMac(mac), "Piblo Desk");
  if (request("/pair/device/request", "POST", payload.c_str(), response) != 200) return false;
  JsonDocument doc;
  if (deserializeJson(doc, response)) return false;
  String id = doc["request_id"] | "";
  std::string code = nod::pair::formatCode(doc["code"] | "");
  if (id.length() < 22 || id.length() > 64 || code.empty()) return false;
  for (char c : id) if (!isalnum((unsigned char)c) && c != '-' && c != '_') return false;
  show(code.c_str(), "Check code and Allow on PC");
  uint32_t start = millis();
  while (millis() - start < nod::pair::kGiveUpMs && WiFi.status() == WL_CONNECTED) {
    delay(nod::pair::kPollEveryMs);
    int result = request("/pair/device/request/" + id, "GET", "", response);
    if (result == 404) break;
    doc.clear();
    if (result != 200 || deserializeJson(doc, response)) continue;
    String state = doc["status"] | "";
    if (state == "denied" || state == "expired") break;
    if (state == "approved") {
      String issued = doc["token"] | "";
      int issuedPort = doc["port"] | (int)port;
      if (!issued.length() || issued.length() > 256 || issuedPort < 1 || issuedPort > 65535) break;
      port = issuedPort; token = issued;
      if (!prefs.putString("host", host) || !prefs.putUShort("port", port) || !prefs.putString("token", token)) {
        token = ""; show("Could not save pairing"); return false;
      }
      show("Paired");
      return true;
    }
  }
  show("Pairing ended", "Send pair over USB");
  return false;
}

void drawState(const DeskState& state) {
  display.fillScreen(BLACK);
  display.setTextColor(WHITE);
  display.setTextSize(2);
  display.setCursor(6, 8); display.println("Piblo");
  display.setTextSize(1);
  display.setCursor(6, 42); display.println(state.bState);
  display.setCursor(6, 60); display.println(state.bLine);
  display.setCursor(6, 108); display.printf("Working: %ld", (long)state.working);
  display.setCursor(6, 124); display.printf("Waiting: %ld", (long)state.waiting);
  if (state.hasTask) { display.setCursor(6, 156); display.println(state.taskTitle); }
  if (state.hasFocus && state.focusActive) {
    char remaining[8]; deskMmss(remaining, sizeof remaining, state.focusRemain);
    display.setCursor(6, 210); display.printf("Focus %s", remaining);
    display.setCursor(6, 226); display.println(state.focusLabel);
  }
  Serial.printf("desk: live state=%s working=%ld waiting=%ld\n", state.bState, (long)state.working, (long)state.waiting);
}

void serialInput() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') { if (input.length() < 256 && !inputOverflow) input += c; else inputOverflow = true; continue; }
    JsonDocument doc;
    if (!inputOverflow && !deserializeJson(doc, input)) {
      if (doc["pair"] == true) {
        token = ""; prefs.remove("token"); pairingAttempted = false; nextTry = 0;
      }
      String s = doc["ssid"] | ""; String p = doc["password"] | "";
      if (s.length() && s.length() <= 32 && p.length() <= 63) {
        ssid = s; password = p;
        prefs.putString("ssid", ssid); prefs.putString("password", password);
        token = ""; prefs.remove("token"); pairingAttempted = false;
        WiFi.disconnect(); WiFi.begin(ssid.c_str(), password.c_str());
        show("Connecting Wi-Fi"); nextTry = millis() + 15000;
      }
    }
    input = ""; inputOverflow = false;
  }
}

void setup() {
  Serial.begin(115200);
  display.begin(); pinMode(46, OUTPUT); digitalWrite(46, HIGH);
  if (!prefs.begin("piblo-desk", false)) { show("Storage error"); return; }
  storageReady = true;
  ssid = prefs.getString("ssid"); password = prefs.getString("password");
  host = prefs.getString("host"); port = prefs.getUShort("port"); token = prefs.getString("token");
  WiFi.mode(WIFI_STA); WiFi.persistent(false);
  if (ssid.length()) { WiFi.begin(ssid.c_str(), password.c_str()); show("Connecting Wi-Fi"); }
  else show("Send Wi-Fi over USB", "See desk setup guide");
}

void loop() {
  if (!storageReady) { delay(1000); return; }
  serialInput();
  if ((int32_t)(millis() - nextTry) < 0) { delay(10); return; }
  nextTry = millis() + 10000;
  if (WiFi.status() != WL_CONNECTED) {
    show("Wi-Fi disconnected", "Send Wi-Fi over USB");
    if (ssid.length()) WiFi.reconnect();
    return;
  }
  if (!token.length() || !host.length() || !port) {
    if (!pairingAttempted) { pairingAttempted = true; if (!pairCore()) show("Pairing not complete", "Send pair over USB"); }
    return;
  }
  String response;
  int status = request("/desk/state", "GET", "", response, true);
  DeskState state;
  if (status == 200 && deskParse(response.c_str(), response.length(), state) && state.coreOk) drawState(state);
  else show(status == 401 ? "Pairing rejected" : "Core unavailable", "Retrying shortly");
}
