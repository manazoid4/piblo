# Piblo Flow improvement loop ledger

One row per `scripts/flow_loop.py` run; details in `docs/evidence/flow-loop/<timestamp>.json`.
All audio is SYNTHETIC (SAPI + sherpa-onnx VCTK voices, numpy noise). No other dictation product was measured.
The last column is the regression gate: WER on the 60 v1 phrases, clean SAPI voices.

| date | git sha | engine | corpus | conditions | WER % (clean) | WER % (noisy) | p50 ms | p95 ms | CPU % | RAM MB | rules learned | decoys learned | held-out WER % before to after | regressions | v1 clean SAPI WER % (gate) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-26 22:07 | 9d5af92 | parakeet | v1 gate 60 + v2 191 x1 | 14 voices x 11 noise; n=251 | 7.91 | 20.85 | 540 | 978 | 635 | 899 | 26 (stub learner) | 0 | 19.5 to 18.45 | 0 | 7.75 |
| 2026-11-27 00:04 | 860bfb0 | parakeet | v1 gate 60 + v2 191 x1 | 14 voices x 11 noise; n=251 | 7.91 | 21.76 | 491 | 723 | 698 | 877 | 5 (real learner) | 0 | 19.76 to 19.5 | 0 | 7.75 |
| 2026-11-27 00:13 | 07d074a | parakeet | v1 gate 60 + v2 191 x5 | 14 voices x 11 noise; n=1015 | 9.81 | 22.0 | 465 | 655 | 699 | 900 | 19 (real learner) | 0 | 21.26 to 20.95 | 0 | 7.75 |
