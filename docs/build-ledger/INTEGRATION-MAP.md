# Piblo Integration Map

```
┌────────────────────────────────────────────────────────┐
│               Physical Piblo Standard                  │
│       (ESP32-S3 / Cardputer / Waveshare Hardware)      │
└──────────────────────────┬─────────────────────────────┘
                           │ (USB / ESP-NOW / Wi-Fi)
                           ▼
┌────────────────────────────────────────────────────────┐
│                      Piblo Core                        │
│   (Local Event Bus, Capability Registry, Tier 0-3 AI)  │
└──────┬───────────────────┬─────────────────────┬───────┘
       │                   │                     │
       ▼                   ▼                     ▼
┌──────────────┐   ┌───────────────┐   ┌─────────────────┐
│  Piblo Flow  │   │  PibloCraft   │   │ Host Companion  │
│ (Local STT)  │   │Mission Control│   │(Windows/Android)│
└──────────────┘   └───────────────┘   └─────────────────┘
```

- **Core Event Bus:** Normalized asynchronous event stream carrying physical inputs, voice commands, agent actions, and completion statuses.
- **Capability Registry:** Standardized schema for computer control, browser automation, screen perception, personal organization, and communication.
- **Assist pipeline (24 Nov):** handheld `/turn`, Android, PibloCraft and scripts call `POST /assist`; Core publishes `assist.ack/routed/awaiting_approval/say/done/cancelled/failed` and `craft.resume.requested` on the EventBus; sensitive steps go to the Buddy approval queue and the device's signed allow; the Android companion feeds `/finddevices/sightings` from its BLE scan.
