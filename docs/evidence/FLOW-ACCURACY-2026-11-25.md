# Piblo Flow accuracy and speed: 25 Nov 2026

All clips are SYNTHETIC (Windows SAPI voices, three speaking rates), not a person and not the device mic.
They prove the Core pipeline and catch regressions. Real-mic accuracy still needs a person.
Machine: Windows 11 PC, CPU only. No device connected.

## Method
- `scripts/flow_corpus.jsonl`: 60 dictation phrases (25+ paste, 10 note, 10 remind, 10 Claude, 5 tricky
  negatives such as "notebook", "Claudette", "this reminds me of"). Written by a free model (NVIDIA Nemotron 3
  Ultra), checked by hand.
- `scripts/flow_accuracy_bench.py`: speaks each phrase with SAPI, posts it to a throwaway Core's `/dictate`
  (real Parakeet STT, real cleanup), compares text and intent. `--local stream|parakeet` runs the engine and
  Flow cleanup in-process instead.

## Results (Core `/dictate`, Parakeet)
| | Intent right | Word error | Exact text | p50 | p95 |
|---|---|---|---|---|---|
| Before (main + #193) | 56/60 | 15.5% | 16/60 | 427 ms | 499 ms |
| After | 60/60 | 3.6% | 42/60 | 451 ms | 545 ms |

Latency = request in to cleaned text out (no typing). Target is 1500 ms first audio byte to typed text.

## What changed
- Dictation no longer waits for the voice command router (it called a local model before typing, up to 30 s). The router now runs after typing, rules only: dictation never calls a local model.
- Built-in name fixes: PibloCraft, Vercel, Tailscale, GitHub, more Piblo Flow spellings; default dictionary
  has MAZos and FlowLens aliases.
- "at 1500" -> "at 15:00", "0300 UTC" -> "03:00 UTC"; amounts ("about 1500 people", "800 by 1200") untouched.
- UK spelling for common prose words, setting `spelling` uk|us (default uk); code and CSS words untouched.
- Text after note / remind me / Claude starts with a capital; "Claw" / "Claudum" at the start count as Claude;
  "Note" glued to the next word is split.
- Speech models warm at start-up in private mode too.

## PC push-to-talk engine
| Engine (`--local`) | Word error | After release |
|---|---|---|
| Streaming zipformer | 43.1% | 52 ms |
| Parakeet | 3.6% | 445 ms |

Streaming loses names, tech words and punctuation, so `nodflow.py` now defaults to Parakeet;
`PIBLO_PTT_ENGINE=stream` opts in.

## Still wrong (not fixed on purpose)
- "Groq" heard as "Grok": Grok is a real product too. Add `Groq: Grok` to your own dictionary if you never mean Grok.
- "Codex" heard as "codec", "licence" (noun) left as "license", "version 2.1.0" heard as "210".
- Some misses are SAPI saying "um" oddly ("Ama", "I'm the"); a real voice may differ.

## Not verified
- Typing into a real Windows window: this PC's screen was locked, so the paste test (`scripts/flow_e2e.py`)
  aborted safely. The paste path is unit tested and was proven on a real PC on 14 Nov.
- Device mic, Wi-Fi upload and a real voice.
