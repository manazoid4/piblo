# Piblo feature build — 13 November 2026

User selected Piblo's own microphone with PC wake-word detection. Exactly two
Terra agents built the first pieces; two fresh Terra agents then handled site
cleanup and an additional distinct PC feature, as explicitly requested.

## Configuration

- Wake word: optional `pip install sherpa-onnx`; run `python scripts/setup-wakeword.py <new-model-directory>`; set `MAZ_WAKEWORD_MODEL_DIR` to that directory. See WAKEWORD-SETUP.md. No model weights are in Git. Model redistribution rights remain unresolved.
- Home PC: `MAZ_VAULT_DIR` selects ONE folder. No folder is enabled by default. Supported document/photo types only, originals up to 10 MiB. Do not use private passports for a public demo.
- Travelling PC: instead of a local folder, set `MAZ_VAULT_REMOTE_URL=http://<home-tailscale-ip>:8787` and `MAZ_VAULT_REMOTE_TOKEN` to the home Core token. The token stays in local settings and HTTP headers, never URLs. Only Tailscale hosts/addresses or loopback are accepted. Existing private-mode policy still applies.
- Shortcuts: `MAZ_SHORTCUTS_FILE` points at an owner-written JSON map, e.g. `{"check server health":"http://127.0.0.1:9000/health"}`. This is a fixed empty POST, never a voice-generated URL. All invocations require device approval. A command entry is `{"command":"Write-Output ready","cwd":"C:/work"}`; it ALSO requires an existing signed project/PC authority grant registered with `POST /shortcuts/authorise`. Grants stay in memory and are checked again at execution. See voice-shortcuts.md.

## Conversation and UI

Normal Ask/PTT recognises exact configured shortcut names and explicit fetch
phrases. Cloud-ASR Live calls the same handler. Direct local/private LFM
speech-to-speech cannot intercept these intents and remains unsupported for
shortcut/file voice routing. It still supports the local wake-word gate.

Wake-word mode is opt-in in an ALREADY OPEN Live app. Say Hey Piblo, pause, then
give the command. Firmware still says listening while armed; no wake chime.
This is not an always-on Home-screen or low-power wake-word implementation.

Windows Memory has Fetch from Home; files download and supported photos/text
preview inline. No new privileged Electron IPC was added. Show me once lists
finished captures, drafts from local model/narration/marks, and requires an
explicit review/save click. Saving never runs the workflow; frames are not
analysed by the model. See app/docs/show-me-once.md.

## Evidence

- 177 app tests and TypeScript passed; main/renderer builds passed.
- Focused backend suites cover wake-word gating, no pre-wake upstream audio,
  re-arm, voice intent precedence, device-signed approvals, one-time action
  execution, signed-grant PowerShell execution, auth, root boundaries, a
  post-lookup link-swap attack, and remote home API forwarding.
- `node app/scripts/verify-home-files.mjs` and `verify-teach.mjs` (run FROM app)
  exercise Electron/IPC with clearly labelled fixture data: original-byte
  download, preview, draft, discard, explicit approval and saved workflow.
- Actual detector: Windows Hazel/Zira synthetic Hey peeblo detected 2/2 at gain
  1; gain 10 clipped/missed one. The final gate feeds unboosted PCM to detection.
  Twenty tested negative utterances across both gains had no activations. CPU
  measured ~0.066 CPU seconds per audio second; this tiny synthetic test is not
  a device accuracy claim.
- No real user files, recording or device configuration were changed. The
  device was observed in Beam and was left alone because another agent is active.

## Three useful clips to record after live verification

1. Physical approval between voice and a real PC action: say the configured
   health shortcut; show it waiting; press No (nothing runs), then Yes on a fresh
   request and show the actual result. Do not imply arbitrary AI shell access.
2. Fetch a harmless sample insurance document from an actual second PC over
   Tailscale; show it downloaded intact without cloud storage. Label the file demo.
3. Narrate a PC workflow, create the draft, correct/review it and save SKILL.md.
   Describe it as a reviewed procedure, not automatic replay or visual learning.

Wake-word footage needs an actual mic trial first. Synthetic audio must be
labelled synthetic. No release without Maz's approval.
