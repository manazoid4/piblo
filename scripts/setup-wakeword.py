"""Download the pinned English wakeword model into a new local directory.

Core's "Hey Piblo" gate (MAZ_WAKEWORD, on by default) looks in %LOCALAPPDATA%/Piblo/wakeword-model
unless MAZ_WAKEWORD_MODEL_DIR says otherwise; without the model Live voice listens ungated.

Model: sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01, from the k2-fsa/sherpa-onnx GitHub
release "kws-models" (an icefall Zipformer transducer trained on GigaSpeech XL).
Licence, as found in its sources (checked 13 Nov 2026, not legal advice):
- sherpa-onnx (the runtime, PyPI sherpa-onnx) is Apache-2.0.
- The archive has no LICENSE/NOTICE file; third-party notices report its bundled README declares
  Apache-2.0. The upstream docs state no model licence, and licence questions are open upstream
  (k2-fsa/sherpa-onnx issues 3760, 3802, 3878).
- GigaSpeech's dataset terms restrict the data to non-commercial research and say a trained model's
  licence is separate.
Piblo does not redistribute the weights: this script downloads them on the owner's PC.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import tarfile
import tempfile
from urllib.request import urlopen

MODEL = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{MODEL}.tar.bz2"
SHA256 = "f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a"
SUFFIX = "epoch-12-avg-2-chunk-16-left-64.int8.onnx"
FILES = ("tokens.txt", *(f"{part}-{SUFFIX}" for part in ("encoder", "decoder", "joiner")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="new model directory")
    destination = parser.parse_args().destination.resolve()
    if destination.exists():
        parser.error(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        tmp = Path(tmp)
        archive = tmp / "model.tar.bz2"
        with urlopen(URL, timeout=60) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
            raise ValueError("model archive checksum mismatch")
        stage = tmp / "model"
        stage.mkdir()
        with tarfile.open(archive, "r:bz2") as tar:
            members = tar.getmembers()
            for name in FILES:
                matches = [m for m in members if m.name == f"{MODEL}/{name}"]
                if len(matches) != 1 or not matches[0].isfile():
                    raise ValueError(f"missing or unsafe model file: {name}")
                # Copy only exact allowlisted regular files; no archive paths are extracted.
                with tar.extractfile(matches[0]) as source, (stage / name).open("wb") as out:
                    shutil.copyfileobj(source, out)
        stage.rename(destination)
    print(f"Model installed: {destination}")


if __name__ == "__main__":
    main()
