"""Flow learns from the user's own corrections, locally, with no manual upkeep.

After Flow pastes, the focused field is read back a few seconds later; if the user fixed a
misheard word, the (misheard -> term) pair is counted. Two consistent sightings (or one strong
phonetic match on a capitalised/technical word) promote it to a dictionary line that
``apply_dictionary`` merges in. Style edits (case, punctuation, synonyms, rewrites) are ignored.

Only word pairs and counts are stored (``learned.json``), never texts. Every rule can be undone,
and ``guard`` demotes any rule that makes a known-good example worse.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

_TOKEN = re.compile(r"[\w'’-]+")
_SECRET = re.compile(r"^(?:sk-|ghp_|gho_|xox|AKIA|eyJ)|^[0-9a-fA-F]{24,}$")
# Function words are never learned as "misheard": a rule on them would rewrite everything.
_COMMON = set("""a an the and or but if so to of in on at by for with from as is are was were be been it its this that
these those i you he she we they me my your our their his her them us do does did not no yes can could will would
should may might must have has had there here then than when what which who how why now just also very more most
some any all one two""".split())


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def phonetic(word: str) -> str:
    """Small deterministic metaphone-ish key: similar-sounding spellings share a key."""
    w = re.sub(r"[^a-z0-9]", "", word.lower())
    for a, b in (("kn", "n"), ("gn", "n"), ("pn", "n"), ("wr", "r")):
        if w.startswith(a):
            w = b + w[2:]
    w = w.replace("ph", "f").replace("ck", "k").replace("q", "k").replace("x", "ks").replace("z", "s").replace("v", "f")
    w = re.sub(r"c(?=[eiy])", "s", w).replace("c", "k")
    head, rest = w[:1], re.sub(r"[aeiouyhw]", "", w[1:])
    return re.sub(r"(.)\1+", r"\1", head + rest)


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def alike(old: str, new: str) -> tuple[bool, bool]:
    """(sounds or spells alike, strong phonetic match) for two word spans."""
    a, b = old.lower().replace(" ", ""), new.lower().replace(" ", "")
    if a == b:
        return False, False
    ka, kb = phonetic(a), phonetic(b)
    strong = bool(ka) and (ka == kb or _ratio(ka, kb) >= 0.85)
    ok = strong or (_ratio(ka, kb) >= 0.8 and _ratio(a, b) >= 0.5) or _ratio(a, b) >= 0.75
    return ok, strong


def looks_secret(text: str) -> bool:
    for t in tokens(text):
        if _SECRET.search(t):
            return True
        if len(t) >= 20 and re.search(r"\d", t) and re.search(r"[A-Za-z]", t):
            return True
    return False


def _technical(word: str) -> bool:
    return word[:1].isupper() or bool(re.search(r"\d", word)) or bool(re.search(r"[a-z][A-Z]", word))


def corrections(pasted: str, final: str) -> list[tuple[str, str, bool, bool]]:
    """(misheard, term, strong, technical) for each genuine transcription fix, else []."""
    p, f = tokens(pasted), tokens(final)
    pl, fl = [t.lower() for t in p], [t.lower() for t in f]
    sm = difflib.SequenceMatcher(None, pl, fl, autojunk=False)
    if not p or not f or sm.ratio() < 0.6:  # the user rewrote it
        return []
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "replace" or not (1 <= i2 - i1 <= 3 and 1 <= j2 - j1 <= 3):
            continue
        old, new = " ".join(p[i1:i2]), " ".join(f[j1:j2])
        ok, strong = alike(old, new)
        if not ok or len(old.replace(" ", "")) < 3 or old.lower() in _COMMON:
            continue
        out.append((old.lower(), new, strong, _technical(new) and i1 > 0))
    return out


def find_span(pasted: str, field: str) -> str | None:
    """The part of ``field`` that is (an edited version of) ``pasted``, or None."""
    p, f = tokens(pasted), tokens(field)
    if len(p) < 2 or len(f) > 5000:
        return None
    pl, fl = [t.lower() for t in p], [t.lower() for t in f]
    m = difflib.SequenceMatcher(None, pl, fl, autojunk=False).find_longest_match(0, len(pl), 0, len(fl))
    if m.size < 2:
        return None
    start, best = m.b - m.a, (0.0, None)
    for s in range(max(0, start - 2), start + 3):
        for n in range(max(1, len(p) - 3), len(p) + 4):
            win = fl[s:s + n]
            if win:
                r = _ratio(" ".join(pl), " ".join(win))
                if r > best[0]:
                    best = (r, " ".join(f[s:s + n]))
    return best[1] if best[0] >= 0.6 else None


class Learner:
    def __init__(self, dir: Path):
        self.path = Path(dir) / "learned.json"
        self._lock = threading.RLock()
        self.version = 0  # bumps whenever the active rule set changes
        self._cache: dict[str, list[str]] = {}
        try:
            self._e = json.loads(self.path.read_text(encoding="utf-8")).get("entries", [])
        except (OSError, ValueError, AttributeError):
            self._e = []

    def _save(self) -> None:
        self.version += 1
        self._cache = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".learned-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as s:
                json.dump({"v": 1, "entries": self._e}, s, indent=1)
            os.replace(tmp, self.path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    @staticmethod
    def _conf(e: dict) -> float:
        eff = e["seen"] + (1 if e.get("strong") else 0)
        return round(max(0.0, min(1.0, (eff - 2 * e.get("conflicts", 0)) / 2)), 2)

    def _view(self, e: dict) -> dict:
        return {"id": e["id"], "term": e["term"], "misheard": e["misheard"], "app": e["app"],
                "confidence": self._conf(e), "seen": e["seen"], "status": e["status"], "created": e["created"]}

    def _settle(self, e: dict, evs: list[dict]) -> None:
        c = self._conf(e)
        if e["status"] == "candidate" and c >= 1:
            e["status"] = "active"
            evs.append({"event": "promoted", **self._view(e)})
        elif e["status"] == "active" and c <= 0:
            e["status"] = "candidate"

    def observe(self, pasted: str, final: str, app: str = "") -> list[dict]:
        if looks_secret(pasted) or looks_secret(final):
            return []
        app, evs = app.lower(), []
        with self._lock:
            for old, term, strong, technical in corrections(pasted, final):
                if any(e["misheard"] == old and e["term"] == term and e["status"] in ("undone", "rejected") for e in self._e):
                    continue  # the user undid it (or the guard rejected it): do not learn it again
                same = [e for e in self._e if e["misheard"] == old and e["status"] not in ("undone", "rejected")]
                hit = next((e for e in same if e["term"] == term and e["app"] in ("", app)), None)
                if hit is None:
                    clash = next((e for e in same if e["app"] == "" and e["term"] != term), None)
                    scope = app if clash and app and app not in clash["apps"] else ""
                    hit = {"id": uuid.uuid4().hex[:8], "term": term, "misheard": old, "app": scope, "seen": 0,
                           "conflicts": 0, "strong": False, "apps": [], "status": "candidate", "created": int(time.time())}
                    self._e.append(hit)
                    evs.append({"event": "candidate", **self._view(hit)})
                hit["seen"] += 1
                hit["strong"] = hit["strong"] or (strong and technical)
                if app and app not in hit["apps"]:
                    hit["apps"] = (hit["apps"] + [app])[-8:]
                for e in same:  # a different answer for the same word contradicts the others in this scope
                    if e is not hit and e["term"] != term and e["app"] == hit["app"]:
                        e["conflicts"] += 1
                        evs.append({"event": "conflict", **self._view(e)})
                        self._settle(e, evs)
                self._settle(hit, evs)
            if evs:
                self._save()
        return evs

    def rules(self, app: str = "") -> list[str]:
        app = app.lower()
        with self._lock:
            if app not in self._cache:
                act = [e for e in self._e if e["status"] == "active" and e["app"] in ("", app)]
                act.sort(key=lambda e: e["app"] != "")  # app-specific last so it wins
                self._cache[app] = [f"{e['term']}: {e['misheard']}" for e in act]
            return self._cache[app]

    def learned(self) -> list[dict]:
        with self._lock:
            return [self._view(e) for e in self._e]

    def undo(self, rule_id: str) -> bool:
        with self._lock:
            for e in self._e:
                if e["id"] == rule_id and e["status"] != "undone":
                    e["status"] = "undone"
                    self._save()
                    return True
        return False

    def guard(self, corpus: list[tuple[str, str]], base: list[str] | None = None) -> list[str]:
        """Reject any active rule that makes a (heard, expected) pair worse than without it."""
        from .flow import apply_dictionary

        base, demoted = list(base or []), []
        with self._lock:
            for e in self._e:
                if e["status"] != "active":
                    continue
                others = base + [f"{x['term']}: {x['misheard']}" for x in self._e if x["status"] == "active" and x is not e]
                mine = others + [f"{e['term']}: {e['misheard']}"]
                if any(_wer(apply_dictionary(h, mine), x) > _wer(apply_dictionary(h, others), x) for h, x in corpus):
                    e["status"] = "rejected"
                    demoted.append(e["id"])
            if demoted:
                self._save()
        return demoted


def _wer(a: str, b: str) -> int:
    x, y = [t.lower() for t in tokens(a)], [t.lower() for t in tokens(b)]
    prev = list(range(len(y) + 1))
    for i, tx in enumerate(x, 1):
        cur = [i]
        for j, ty in enumerate(y, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (tx != ty)))
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------- Windows readback (UI Automation)
def read_focused() -> tuple[str, bool, str] | None:
    """(field text, is_password, process name) of the focused control, or None on any failure."""
    try:
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as ua

        uia = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=ua.IUIAutomation)
        el = uia.GetFocusedElement()
        if el.CurrentIsPassword:
            return "", True, ""
        try:  # ValuePattern (edits), then TextPattern (documents, rich text)
            text = el.GetCurrentPattern(10002).QueryInterface(ua.IUIAutomationValuePattern).CurrentValue or ""
        except Exception:  # noqa: BLE001
            text = el.GetCurrentPattern(10014).QueryInterface(ua.IUIAutomationTextPattern).DocumentRange.GetText(20000) or ""
        return text, False, process_name(el.CurrentProcessId)
    except Exception:  # noqa: BLE001 - readback is best effort
        return None


def process_name(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wintypes.HANDLE
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf, n = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
        ok = k.QueryFullProcessImageNameW(wintypes.HANDLE(h), 0, buf, ctypes.byref(n))
        return Path(buf.value).stem.lower() if ok else ""
    finally:
        k.CloseHandle(wintypes.HANDLE(h))


def focused_app() -> str:
    """Process name of the foreground window ("" when unknown)."""
    try:
        import ctypes
        from ctypes import wintypes

        u = ctypes.windll.user32
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(u.GetForegroundWindow(), ctypes.byref(pid))
        return process_name(pid.value)
    except Exception:  # noqa: BLE001
        return ""


def watch_paste(learner: Learner, pasted: str, app: str, delay: float = 8.0, reader=read_focused) -> threading.Thread:
    """After ``delay`` s read the focused field back and learn from any fix. Never raises."""
    def run() -> None:
        try:
            time.sleep(delay)
            got = reader()
            if not got or got[1] or (app and got[2] and got[2] != app):
                return
            span = find_span(pasted, got[0])
            if span:
                learner.observe(pasted, span, app)
        except Exception:  # noqa: BLE001
            pass

    t = threading.Thread(target=run, daemon=True, name="flow-learn")
    t.start()
    return t
