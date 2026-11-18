# Training on Kaggle (free GPU)

Notebooks: `hey_piblo_train.ipynb` (PC wake word), `hey_piblo_micro.ipynb` (on-device wake word), `piblo_persona_lora.ipynb` (Talk persona). Plan: `PLAN.md`.
Core uses `%LOCALAPPDATA%\Piblo\wakeword-model\hey_piblo.onnx` when it exists; otherwise it uses `hey_jarvis`.

By hand: kaggle.com → Create → Import Notebook → this file → GPU T4 + Internet On → Run All → Output tab.

From this PC, once a Kaggle API token is in `~/.kaggle/kaggle.json`:

```
# replace KAGGLE_USERNAME in kernel-metadata.json, then:
kaggle kernels push -p training/kaggle
kaggle kernels status <user>/piblo-hey-piblo-wakeword
kaggle kernels output <user>/piblo-hey-piblo-wakeword -p "%LOCALAPPDATA%\Piblo\wakeword-model"
```

The same folder pattern (notebook + kernel-metadata.json) works for any other model Piblo needs trained.
