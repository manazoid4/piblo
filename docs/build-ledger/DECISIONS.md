# Piblo Architectural Decisions

1. **Local-First & Small-Model Routing:** Emphasize deterministic Tier 0 handlers and small local models before escalating to larger models or cloud services.
2. **Extend PibloCraft over New UIs:** Use PibloCraft as the central mission-control dashboard rather than creating competing dashboards.
3. **Cream Default Concept:** Adopt cream as the default visual concept with smoke finish as an interactive switch option.
4. **Lightweight Event Bus:** Avoid heavy message brokers in favor of a clean asynchronous event stream suitable for local host and companion devices.
