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
