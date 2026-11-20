# Flow streaming dictation, v1.5 PC proofs and PibloCraft bridge: 19 Nov 2026

All clips are SYNTHETIC (Windows SAPI voices Hazel and Zira, three speaking rates, both female), not a person and not the
Cardputer mic. They prove the pipeline and the relative speed; real-mic accuracy is not proven (needs Maz, see bottom).
Machine: Maz's Windows 11 PC, CPU only, 2 threads for the streaming model.

## 1. Latency and accuracy (20 Maz-style commands)
Command: `python scripts/flow_stream_bench.py --json out.json` (Core venv python). Latency = key release -> final text.
Streaming engines are fed at real-time pace in 100 ms chunks; the clock starts when the last chunk is handed over.

| Engine | Licence | p50 | p95 | WER vs reference |
|---|---|---|---|---|
| Today: Parakeet TDT 0.6b v3 batch (Flow `/dictate`) | CC-BY-4.0 | 456 ms | 558 ms | 5.3% |
| Local Whisper small.en int8 (baseline) | MIT | 3667 ms | 3816 ms | 5.2% |
| **Streaming zipformer en 2023-06-26 int8 (chosen)** | Apache-2.0 (sherpa-onnx + icefall model) | **67 ms** | **77 ms** | 16.9% |
| Streaming zipformer en 20M int8 | Apache-2.0 | 31 ms | 42 ms | 47.3% |
| NeMo streaming fast-conformer 80 ms (fp32, 456 MB) | NVIDIA NeMo model, licence not checked | 282 ms | 366 ms | 12.4% |

VAD end-of-speech mode (trailing-silence rule 0.6 s, measured separately): chosen model p50 437 ms, p95 685 ms.
That silence wait is why hands-free is slower than hold-to-talk. It is only measured in the bench; no live VAD
dictation mode exists in nodflow.

Full path on this PC, real focus guard and real Ctrl+V into a Tk text box (`python host/nodflow.py --stream-test clips...`):
20 clips, 14 pasted (text landed word for word), 6 saved as note/remind intents.
`python host/nodflow.py --stats` => `ptt: n=20 p50=65 ms p95=120 ms` (release to Ctrl+V sent). Targets p50 < 150 ms and p95 < 250 ms are met.

Honest reading: speed target met with margin, accuracy target NOT met. The streaming model is 3x worse than Parakeet
(names like Claude, Piblo are the worst; a few built-in aliases were added). "As good as OpenAI" is unmeasured: OpenAI is
a paid call and was not made. Batch Parakeet stays the accurate path (`PIBLO_PTT_ENGINE=batch`).

## 2. What was built
- `host/mazhost/stt_stream.py`: streaming recognizer (sherpa-onnx, revision-pinned). `scripts/setup-stream-stt.py` fetches the model (72 MB) into `%LOCALAPPDATA%\Piblo\stt-stream`.
- `host/nodflow.py` (the Right-Ctrl PC agent): when the model is installed, audio is decoded while the key is held; on release it flushes, runs the existing Flow `finish()` (cleanup, dictionary, prefix routing, notes) and `safe_paste()` (focus guard), and logs a `ptt` line with `release_to_text` to `latency.log`.
- CLI for PibloCraft to call: `python host/nodflow.py --stats [--json]` prints p50/p95 per mode (`{"ptt": {"n":..,"p50_ms":..,"p95_ms":..}}`). A `pc say --stats` can shell out to it. Fallback is Win+H.
- `paste(..., on_typed=)` reports the instant Ctrl+V is sent, so the latency excludes the 0.4 s clipboard restore.
- Talk (device voice): `MAZ_LIVE_STREAM_STT=1` decodes the turn while the user speaks and skips the batch ASR call. Default off. Not measured live on the device.

## 3. v1.5 items a PC can prove
| NOD_STATUS item | Proof | Result |
|---|---|---|
| 3 Saved notes survive a restart | `pytest host/tests/test_core_restart_proof.py`: real Core process, POST /memory, process killed, started again, GET /memory/recall finds it (temp HOME) | pass. The device Notes app itself is not covered |
| 4 One useful PC action with approval | Bridge below: device approval gates a job sent to PibloCraft; `pytest host/tests/test_piblocraft_bridge.py` (allow sends, deny never sends, command logged) | pass. Approval is simulated through `/buddy/decide`, not pressed on the device |
| 5 Core serves the right build | Live read-only GET on the running Core: `/fw/manifest` = 1.5.4 / fba9107, `/fw/latest.bin` 1395296 B, sha256 equals the manifest | Core serves a self-consistent build, but it is NOT the newest main firmware (4 firmware commits after fba9107: #175, #171, #169, #151). Stays yellow |
| 2 Faster voice turn | Streaming ASR bench above (ASR after end of speech 456 ms -> 67 ms); `test_streamed_stt_hears_the_turn_before_it_ends` | Mechanism proven; a real device turn with the flag on was not measured |

## 4. Bridge (device -> Core -> PibloCraft)
`POST /bridge/job {command, cwd}` logs the command to `<data dir>/bridge.log`, raises a device approval, returns at once.
Only on "allow" does Core POST `{id, command, cwd, requested_by:"piblo-device", approval_required:true}` to `MAZ_PIBLOCRAFT_BRIDGE_URL`.
Deny, expiry or an empty URL: nothing is sent, status says why. `GET /bridge/job/{id}` reports waiting | sent | denied | expired | send_failed | no_receiver.
Payload format was defined here because the receiver doc (piblocraft `docs/bridge-protocol.md`, branch feat/features-1119) was not pushed yet. Nothing in firmware calls `/bridge/job` yet.
