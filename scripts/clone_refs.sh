#!/usr/bin/env bash
# Shallow-clone the reference repos listed in docs/REFERENCE-REPOS.md into ../piblo-refs (or $1).
set -u
dest="${1:-$(cd "$(dirname "$0")/../.." && pwd)/piblo-refs}"
mkdir -p "$dest" && cd "$dest" || exit 1
for repo in \
  MoveCall/claude-desktop-buddy-esp32 78/xiaozhi-esp32 Kaayoos/clawpal \
  MichalZaniewicz/esphome-waveshare-esp32-s3-audio-va n-IA-hane/esphome-audio-stack \
  shravanmali/aura-esp32-voice-assistant iamankushpandit/esp_ai espressif/esp-sr \
  raullenchai/esp32-thinks chrisdunnname/esphome-s3-box-3-lvgl lvgl/lv_port_esp32 \
  mathertel/OneButton tzapu/WiFiManager h2zero/NimBLE-Arduino ayushsharma82/ElegantOTA; do
  name="${repo#*/}"
  if [ -d "$name/.git" ]; then git -C "$name" pull -q --ff-only && echo "updated $repo"; continue; fi
  # blob filter keeps esp-sr's model files from bloating the clone
  git clone -q --depth 1 --filter=blob:limit=5m "https://github.com/$repo.git" && echo "cloned $repo" || echo "FAILED $repo"
done
echo "refs in $dest"
