# MAZ Core v0.7

MAZ Core is the Windows companion for Piblo. The Cardputer stays a small, responsive control surface; CPU/GPU telemetry, local AI, Beam persistence, project context and safe PC actions run here.

## Start

For a complete install use the root `Piblo-v0.7-Install.zip` and double-click `START-HERE.cmd`.

For Core by itself, extract `MAZ-Core-v0.7.zip` and run `INSTALL-MAZ-CORE.cmd`. Existing `.env` configuration is preserved by the normal installer path.

## v0.7 FIELD services

- `GET /system/status` — on-demand cached CPU/RAM/battery, NVIDIA GPU/VRAM/temperature and Ollama residency. There is no background monitoring loop.
- `POST /beam/to-pocket` — queue bounded text/HTTPS links for the Pocket.
- `GET /beam/pull` — Pocket pulls and acknowledges the oldest queued Beam item.
- `POST /beam/from-pocket` — store a Pocket Beam and, on Windows, copy it to the clipboard.
- `/turn/text` and `/turn/raw` accept a bounded Pocket screen context. Context is explicitly treated as untrusted data, never as instructions, and Context Ask cannot execute a deterministic PC/reminder command.

All endpoints require the existing MAZ bearer token. Beam never becomes a remote shell.

## SUBTITLES: local video to .srt

Generates a timestamped `.srt` beside a local video with faster-whisper, then optionally opens it in VLC with the subtitle loaded. Local files only: no streaming, no DRM-protected content.

Configure in `host/.env`:

```
MAZ_MEDIA_ROOTS=C:\Users\me\Videos;D:\Films   # default: your Videos folder
MAZ_SUBTITLE_MODEL=base                       # multilingual; small/medium = slower, better
MAZ_SUBTITLE_TIMEOUT_SECONDS=3600
MAZ_VLC_PATH=                                 # optional; PATH and Program Files are checked
```

- `GET /subtitles/videos` lists recent videos in the media folders (newest first).
- `POST /subtitles/job` with `{"path": "...", "language": "auto" | "en" | "de" ..., "open_vlc": true, "overwrite": false}` starts a job. `path` is absolute or relative to a media folder.
- `GET /subtitles/job/{id}` reports `state` (`queued`, `extracting_audio`, `transcribing`, `done`, `failed`, `cancelled`), `progress` %, a short `label` (e.g. `TRANSCRIBING 42%`), `srt`, `detected_language`, `media_seconds`, and the measured `duration_ms`.
- `POST /subtitles/job/{id}/cancel` cancels; `GET /subtitles/jobs` lists recent jobs.

Safety: paths are resolved (symlinks followed) and must stay inside `MAZ_MEDIA_ROOTS` with a video extension. An existing `<name>.srt` is never overwritten unless `overwrite` is true (`<name>.nod.srt` is written instead). ffmpeg extracts 16 kHz mono audio when installed; otherwise faster-whisper decodes the file directly. Without VLC the job still succeeds and reports `vlc.reason = vlc_not_found`. One transcription runs at a time; further jobs queue.

## Local engines: Ollama or llama.cpp

`MAZ_LOCAL_ENGINE` selects which local runtime answers LOCAL and the local half of AUTO. Routing, profiles, the primary/backup chain and the 160-token answer bound are identical on both.

- `ollama` (default) — Ollama manages the models and the keep-alive window.
- `llamacpp` — MAZ Core calls an already-running `llama-server` over its OpenAI-compatible `/v1/chat/completions`. Start it first:

```powershell
$env:MAZ_LLAMACPP_SERVER_EXE = "C:\path\to\llama-server.exe"
$env:MAZ_LLAMACPP_MODEL_PATH = "C:\path\to\model.gguf"
.\host\start-llamacpp.ps1
```

Then set `MAZ_LOCAL_ENGINE=llamacpp` and `MAZ_LLAMACPP_URL=http://127.0.0.1:8080` in `host/.env`.

MAZ Core never launches inference itself. One long-lived `llama-server` keeps the model resident, which is what makes a follow-up Pocket turn answer in about a second. `keep_alive` in the model status is an Ollama concept and does not apply to llama.cpp; `/props` supplies the context llama-server actually fitted after clamping to VRAM. `MAZ_LLAMACPP_BACKUP_URL` plus `MAZ_LLAMACPP_BACKUP_MODEL` point the backup step of the chain at a second server, typically a smaller model on another port.

The server stays loopback-only. Authenticated MAZ Core remains the single LAN-facing gateway.

## Cloud and the explicit MazLatest route

The CLOUD step remains any configured OpenAI-compatible endpoint. MAZLATEST is a separate fourth route for the locally running 9router:

```
MAZ_MAZLATEST_URL=http://localhost:20128/v1
MAZ_MAZLATEST_KEY=<9router API key, when required>
MAZ_MAZLATEST_MODEL=MazLatest
```

MAZ Core restricts this endpoint to loopback HTTP, keeps the optional router credential on the PC, sends `stream: false`, and reports provider `mazlatest:MazLatest`. The Cardputer only sends `route=mazlatest` to authenticated MAZ Core; it never contacts 9router or stores router credentials. If 9router is unavailable, MAZLATEST fails explicitly and does not fall back to LOCAL, AUTO or CLOUD. `/diagnostics` reports router reachability, whether authentication was accepted or is not required, and whether the configured alias is advertised without returning any credential.

## Local AI profiles

Set `MAZ_AI_PROFILE` to one of:

- `smart` — default. 4096 context for normal Pocket work, expanding only for genuinely larger prompts; 5-minute model keep-alive.
- `save` — 4096-first and unload after each request to free GPU memory.
- `fast` — adaptive context with a 30-minute keep-alive for repeated conversations.

LOCAL still tries configured local models only. AUTO tries local models before optional cloud. MAZLATEST uses only the explicit loopback route. Performance data from Ollama replies is retained in the model status so tuning is evidence-based.

## One-click Beam

`BEAM-TO-POCKET.cmd` calls `beam.ps1`. With no argument it sends the Windows clipboard; with `-Text` it sends the supplied text. It talks only to the locally running authenticated Core endpoint.

## Packaging rule

Every releasable version must produce both complete halves and the combined convenience package:

1. `MAZ-Core-vX.Y.zip`
2. `MAZ-Cardputer-vX.Y.zip`
3. `Piblo-vX.Y-Install.zip`

The firmware inside the Cardputer package remains an app-only M5Launcher image. MAZ Core never makes Piblo self-flash firmware partitions.

## Tests

Run from the repository root:

```powershell
python -m pytest host/tests -q
```

The GitHub Actions firmware workflow also runs the Core suite before compiling the Cardputer image and building the three required ZIP packages.

## Fast push-to-talk dictation (streaming)
`python ..\scripts\setup-stream-stt.py` downloads a 72 MB Apache-2.0 streaming model. `nodflow.py` (hold Right Ctrl) uses the accurate Parakeet batch path by default (~450 ms after release). With the model installed and `PIBLO_PTT_ENGINE=stream` it decodes while you speak and pastes about 50-70 ms after release, but on the 60-phrase spoken bench it got 43% of words wrong against Parakeet's 3.6% (`scripts/flow_accuracy_bench.py --local stream|parakeet`). `python nodflow.py --stats [--json]` prints p50/p95 per mode from `latency.log` (PibloCraft can call this). Talk can use the same model: `MAZ_LIVE_STREAM_STT=1`. Accuracy trade-off and numbers: `docs/evidence/FLOW-STREAMING-AND-BRIDGE-2026-11-19.md`.

## Piblo -> PibloCraft jobs
`POST /bridge/job {command, cwd}` asks for approval on the device, then sends the job to `MAZ_PIBLOCRAFT_BRIDGE_URL`. Nothing is sent without a yes; every command is logged to `bridge.log` in the data folder.
