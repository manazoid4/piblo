"""Small, validated requests for Piblo's existing parametric accessory library."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from . import private_mode
from .nowindow import no_window

HARDWARE = Path(__file__).resolve().parents[2] / "hardware"
LIBRARY = {"stand": "Desk stand", "mount": "Wall or fridge mount", "loop": "Lanyard or keyring loop"}


def parse_request(text: str) -> dict:
    text = text.lower()
    matches = [name for name, words in {"stand": r"\bstand\b", "mount": r"\b(?:mount|fridge|wall)\b",
                                       "loop": r"\b(?:lanyard|keyring|loop)\b"}.items() if re.search(words, text)]
    if len(matches) != 1:
        raise ValueError("Choose one Piblo accessory: desk stand, wall/fridge mount, or lanyard/keyring loop.")
    accessory = matches[0]
    if accessory == "stand":
        angle = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:degrees?|°)", text)
        value = max(30, min(80, float(angle.group(1)))) if angle else 60
        params = {"angle": value}
    elif accessory == "mount":
        params = {"attachment": "magnet" if re.search(r"fridge|magnet", text) else "screw"}
    else:
        params = {"hole_d": 5}
    return {"accessory": accessory, "params": params}


class Accessories:
    def __init__(self, data_dir: Path):
        self.output_dir = data_dir / "maker" / "accessories"

    def library(self) -> dict:
        return {"accessories": [{"id": key, "name": name} for key, name in LIBRARY.items()],
                "text": "Piblo accessories. Fit, strength and printability need physical validation."}

    def make(self, text: str) -> dict:
        request = parse_request(text)
        accessory, params = request["accessory"], request["params"]
        dimensions = (HARDWARE / "case" / "case_dimensions.scad").read_text(encoding="utf-8")
        template = (HARDWARE / "accessories" / f"{accessory}.scad").read_text(encoding="utf-8")
        source = template.replace("include <../case/case_dimensions.scad>", dimensions)
        for key, value in params.items():
            literal = f"{value:g}" if isinstance(value, (int, float)) else json.dumps(value)
            source = re.sub(rf"(?m)^{key}\s*=.*?;", f"{key} = {literal};", source)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        scad = self.output_dir / f"{accessory}-{uuid.uuid4().hex[:12]}.scad"
        scad.write_text(source, encoding="utf-8")
        result = {**request, "format": "scad", "path": str(scad), "scad_path": str(scad),
                  "text": "Open this in OpenSCAD to export. Check fit and strength before use."}
        if executable := shutil.which("openscad"):
            stl = scad.with_suffix(".stl")
            try:
                private_mode.guard_subprocess("OpenSCAD render", local=True)
                process = subprocess.run([executable, "-o", str(stl), str(scad)], capture_output=True,
                                         stdin=subprocess.DEVNULL, timeout=90, check=False, **no_window())
                if process.returncode == 0 and stl.is_file() and stl.stat().st_size > 0:
                    result.update(format="stl", path=str(stl), text="STL rendered on your paired computer. Check fit and strength before use.")
            except (OSError, subprocess.SubprocessError):
                pass
        return result
