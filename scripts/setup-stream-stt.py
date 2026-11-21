"""Fetch the streaming dictation model (Apache-2.0, ~72 MB int8) into %LOCALAPPDATA%\\Piblo\\stt-stream.

  python scripts/setup-stream-stt.py [zipformer-en-2023-06-26 | zipformer-en-20M]

Revision-pinned, like the Parakeet model. Safe to re-run.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from mazhost import stt_stream as s  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else s.DEFAULT_MODEL
repo, rev, _ = s.MODELS[name]
from huggingface_hub import snapshot_download  # noqa: E402

snapshot_download(repo, revision=rev, allow_patterns=s.files(name), local_dir=s.model_dir(name))
print(("ready: " if s.available(name) else "INCOMPLETE: ") + str(s.model_dir(name)))
