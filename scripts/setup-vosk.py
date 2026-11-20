"""Download the small English Vosk model for MAZ_STT_PROVIDER=vosk (about 40 MB, Apache-2.0).

Installs into %LOCALAPPDATA%/Piblo/vosk-model unless a destination is given (Core reads MAZ_VOSK_MODEL_DIR).
"""
import io
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

MODEL = "vosk-model-small-en-us-0.15"
URL = f"https://alphacephei.com/vosk/models/{MODEL}.zip"

dest = Path(sys.argv[1] if len(sys.argv) > 1 else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Piblo" / "vosk-model")
if dest.exists():
    sys.exit(f"already installed: {dest}")
dest.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=dest.parent) as tmp, urlopen(URL, timeout=120) as r:
    zipfile.ZipFile(io.BytesIO(r.read())).extractall(tmp)
    shutil.move(str(Path(tmp) / MODEL), dest)
print(f"Vosk model installed in {dest}")
