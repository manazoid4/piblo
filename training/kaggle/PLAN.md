# Kaggle week plan (30 GPU hours, agreed 18 Nov 2026)

Rule: train only what nobody has built; use ready-made models everywhere else.

## Checked first (no repeats)
- No earlier Kaggle, LoRA, fine-tune or on-device wake word work in Unified Memory, piblo-private or this repo.
- Reused, not redone: `docs/evidence/VOICE-RESEARCH-2026-11.md`, `docs/evidence/voice-bench-2026-11-14.md`.
- Model pick, no GPU needed: open-source Wispr Flow replacements (LocalFlow, Murmur, wishper) clean dictation with a small
  Qwen instruct model. Core already ships Qwen3-1.7B, so it is the dictation cleanup model and the persona base.

## Jobs
| # | Notebook | Gives Piblo | Hours | Done when |
|---|---|---|---|---|
| 1 | `hey_piblo_train.ipynb` (openWakeWord) | "Hey Piblo" for Talk on the PC | ~3 | `hey_piblo.onnx` in `%LOCALAPPDATA%\Piblo\wakeword-model\`, detects on real audio |
| 2 | `hey_piblo_micro.ipynb` (microWakeWord) | "Hey Piblo" on the Cardputer, no audio streamed to the PC | ~5 | `hey_piblo.tflite` + manifest added to the next firmware release (no flash now) |
| 3 | `piblo_persona_lora.ipynb` (Unsloth) | The long Talk persona prompt trained into Qwen3-1.7B: faster first words, steadier Piblo voice, works offline | ~8 | Student with a one-line prompt matches or beats the base with the full prompt on Piblo's rules; GGUF exported |
| - | Reruns with more samples/steps on whichever of 1-3 tests weakest | | rest | |

## Licence warning
The wake word training data (AudioSet, FMA, ACAV100M features, microWakeWord negatives) and openWakeWord's pretrained
`hey_jarvis` are non-commercial. Fine for the beta. The Kickstarter shipping models need commercially clean negatives.

## Needs Maz
- Kaggle API token in `C:\Users\Maz\.kaggle\kaggle.json`, phone-verified account (GPU + internet).
