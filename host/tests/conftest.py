"""Give every test process (each pytest-xdist worker) its own home, so default ~/.maz-pocket
databases are never shared between parallel workers or written into the real home folder."""
import os
import sys
import tempfile

_real_home = os.path.expanduser("~")
if sys.platform != "win32":  # Windows keeps browsers under LOCALAPPDATA, which we leave alone
    _cache = "Library/Caches" if sys.platform == "darwin" else ".cache"
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(_real_home, _cache, "ms-playwright"))

_home = tempfile.mkdtemp(prefix=f"piblo-test-{os.environ.get('PYTEST_XDIST_WORKER', 'main')}-")
os.environ["HOME"] = os.environ["USERPROFILE"] = _home
os.environ["MAZ_DATA_DIR"] = os.path.join(_home, "data")
os.environ["MAZ_BUDDY_DB"] = "off"  # existing suites assume a fresh in-memory Buddy; persistence tests pass buddy_db explicitly
os.environ["MAZ_WAKEWORD"] = "0"  # live tests feed speech directly; a real installed wake word engine would gate them forever (gate tests pass cfgs explicitly)
