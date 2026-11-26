"""Find My Devices: one place to answer "Piblo, find my AirPods".

What this really does, and nothing more:

* Keeps a small book of the user's own devices (name, kind, aliases, which
  location services they are signed into) in Core's control folder.
* Takes Bluetooth sightings that Piblo's own observers report (the Android
  companion's BLE scan, an optional PC scan) and turns signal strength into a
  proximity band with an honest uncertainty, plus a closer/further trend.
* Offers real actions per provider: ring a Piblo phone or handheld through the
  existing alert path, and hand off to Apple Find My or Google Find Hub in the
  user's own signed-in browser or app. Piblo never asks for, stores or
  replays Apple or Google credentials, and never pretends to have a location
  those services did not give it.

Apple accessories paired to an iPhone use rotating random Bluetooth addresses,
so a nearby "AirPods Pro" advert cannot be proven to be *yours*. Those sightings
are reported as "an AirPods-type device", never as your AirPods.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

KINDS = ("earbuds", "headphones", "phone", "tablet", "laptop", "watch", "tag", "handheld", "other")
PROVIDERS = ("apple_findmy", "google_find_hub", "piblo_phone", "piblo_handheld", "bluetooth")
MAX_DEVICES = 50
MAX_SIGHTINGS_PER_DEVICE = 60
MAX_UNASSIGNED = 40
LIVE_WINDOW_S = 20.0      # signal newer than this counts as "in range now"
RECENT_WINDOW_S = 120.0   # older than live but newer than this: "just lost"

APPLE_FINDMY_URL = "https://www.icloud.com/find"
GOOGLE_FIND_HUB_URL = "https://www.google.com/android/find"

# Apple Continuity "proximity pairing" model ids (bytes 3-4 of type 0x07). These
# are public facts documented by Bluetooth research (e.g. furiousMAC/continuity);
# no code is copied. Unknown ids fall back to a generic label.
APPLE_AUDIO_MODELS = {
    "0220": "AirPods (1st gen)",
    "0F20": "AirPods (2nd gen)",
    "1320": "AirPods (3rd gen)",
    "1920": "AirPods (4th gen)",
    "1B20": "AirPods (4th gen, ANC)",
    "0E20": "AirPods Pro",
    "1420": "AirPods Pro (2nd gen)",
    "2420": "AirPods Pro (2nd gen, USB-C)",
    "0A20": "AirPods Max",
    "1F20": "AirPods Max (USB-C)",
    "0320": "Powerbeats3",
    "0B20": "Powerbeats Pro",
    "0C20": "Beats Solo Pro",
    "1120": "Beats Studio Buds",
    "1020": "Beats Flex",
    "0520": "BeatsX",
    "0620": "Beats Solo3",
    "0920": "Beats Studio3",
    "1720": "Beats Studio Pro",
    "1220": "Beats Fit Pro",
    "1620": "Beats Studio Buds+",
}


# --------------------------------------------------------------------------- decoding
def decode_apple_manufacturer(hex_data: str) -> dict | None:
    """Decode Apple (company 0x004C) manufacturer data, company id stripped.

    Returns {"family": "apple_audio", "model": ..., "model_id": ...} for a
    proximity-pairing advert (AirPods/Beats out of the case or case open),
    {"family": "apple_findmy"} for an offline-finding advert, else None.
    """
    try:
        data = bytes.fromhex(hex_data.replace(" ", ""))
    except ValueError:
        return None
    i = 0
    while i + 2 <= len(data):
        kind, length = data[i], data[i + 1]
        body = data[i + 2:i + 2 + length]
        if kind == 0x07 and len(body) >= 3:
            model_id = body[1:3].hex().upper()
            out = {"family": "apple_audio", "model_id": model_id,
                   "model": APPLE_AUDIO_MODELS.get(model_id, "Apple or Beats headphones")}
            # Battery nibbles are best effort: 0-10 = 0-100 %, 15 = unknown.
            if len(body) >= 6:
                pods, case = body[4], body[5] & 0x0F
                left, right = pods >> 4, pods & 0x0F
                battery = {k: v * 10 for k, v in (("pod_a", left), ("pod_b", right), ("case", case)) if v <= 10}
                if battery:
                    out["battery_approx"] = battery
            return out
        if kind == 0x12:
            return {"family": "apple_findmy", "model": "Apple Find My accessory (offline finding)"}
        i += 2 + length
    return None


# --------------------------------------------------------------------------- proximity
BANDS = (
    (-55, "very_close", "Very close, within about a metre"),
    (-67, "close", "Close, probably in the same room"),
    (-80, "nearby", "Nearby, maybe a room or two away"),
    (-200, "edge", "At the edge of Bluetooth range"),
)


def rssi_range_m(rssi: float, tx_power: int | None = None) -> tuple[float, float]:
    """A deliberately wide distance range from the log-distance path-loss model.

    tx_power is the expected RSSI at 1 m (adverts often include it; -59 is a
    common default). Path-loss exponent 2 (open space) to 3.5 (walls, bodies)
    gives the low/high ends, so the answer is honest about how noisy RSSI is.
    """
    ref = tx_power if tx_power is not None and -100 < tx_power < 0 else -59
    near = 10 ** ((ref - rssi) / (10 * 3.5))
    far = 10 ** ((ref - rssi) / (10 * 2.0))
    return round(max(0.1, near), 1), round(min(100.0, max(near, far)), 1)


def proximity(sightings: list[dict], now: float | None = None) -> dict:
    """Band, range and trend from recent sightings (newest last)."""
    now = time.time() if now is None else now
    if not sightings:
        return {"state": "never_seen", "label": "Not seen by Piblo yet", "confidence": "none"}
    last = sightings[-1]
    age = now - float(last["at"])
    live = [s for s in sightings if now - float(s["at"]) <= LIVE_WINDOW_S and s.get("rssi") is not None]
    if not live:
        state = "just_lost" if age <= RECENT_WINDOW_S else "out_of_range"
        label = ("Lost the signal a moment ago" if state == "just_lost"
                 else "Not in Bluetooth range right now")
        return {"state": state, "label": label, "confidence": "none", "last_seen_s": round(age)}
    recent = live[-5:]
    rssi = statistics.median(float(s["rssi"]) for s in recent)
    band = next(b for b in BANDS if rssi >= b[0])
    spread = (max(float(s["rssi"]) for s in recent) - min(float(s["rssi"]) for s in recent)) if len(recent) > 1 else 99
    confidence = "medium" if len(recent) >= 3 and spread <= 10 else "low"
    trend = "steady"
    if len(live) >= 6:
        before = statistics.median(float(s["rssi"]) for s in live[-6:-3])
        after = statistics.median(float(s["rssi"]) for s in live[-3:])
        if after - before >= 4:
            trend = "closer"
        elif before - after >= 4:
            trend = "further"
    lo, hi = rssi_range_m(rssi, recent[-1].get("tx_power"))
    return {"state": "in_range", "band": band[1], "label": band[2], "rssi": round(rssi),
            "range_m": [lo, hi], "trend": trend, "confidence": confidence,
            "observer": last.get("observer", ""), "last_seen_s": round(age), "samples": len(recent)}


# --------------------------------------------------------------------------- store
@dataclass
class Action:
    id: str
    label: str
    provider: str
    kind: str  # ring | handoff | scan
    url: str = ""


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").replace("\x00", "").split())[:limit]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower().replace("'s", "").replace("’s", "")).strip()


class DeviceBook:
    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.RLock()
        self._devices: dict[str, dict] = {}
        self._sightings: dict[str, list[dict]] = {}
        self._unassigned: dict[str, dict] = {}
        self._load()

    # ---- persistence (devices + last sighting; the live buffer is memory only)
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for dev in data.get("devices", [])[:MAX_DEVICES]:
            if isinstance(dev, dict) and dev.get("id"):
                self._devices[dev["id"]] = dev
                if dev.get("last_sighting"):
                    self._sightings[dev["id"]] = [dev["last_sighting"]]

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for dev_id, dev in self._devices.items():
            buf = self._sightings.get(dev_id)
            if buf:
                dev["last_sighting"] = buf[-1]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": list(self._devices.values())}, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- devices
    def add(self, body: dict) -> dict:
        name = _clean(body.get("name"), 60)
        if not name:
            raise ValueError("device_name_required")
        kind = body.get("kind") or "other"
        if kind not in KINDS:
            raise ValueError("device_kind_invalid")
        providers = body.get("providers") or {}
        if not isinstance(providers, dict) or any(p not in PROVIDERS for p in providers):
            raise ValueError("device_provider_invalid")
        bt = providers.get("bluetooth") or {}
        if bt:
            providers["bluetooth"] = {
                "addresses": [_clean(a, 17).upper() for a in bt.get("addresses", [])][:4],
                "names": [_clean(n, 60) for n in bt.get("names", [])][:4],
                "apple_model_id": _clean(bt.get("apple_model_id", ""), 4).upper(),
            }
        with self._lock:
            if len(self._devices) >= MAX_DEVICES and body.get("id") not in self._devices:
                raise OverflowError("device_book_full")
            dev_id = _clean(body.get("id"), 40) or "dev-" + uuid.uuid4().hex[:8]
            dev = {"id": dev_id, "name": name, "kind": kind,
                   "aliases": [_clean(a, 40) for a in body.get("aliases", [])][:6],
                   "providers": providers, "added_at": self.clock()}
            self._devices[dev_id] = dev
            self._save_locked()
            return self.describe(dev_id)

    def remove(self, dev_id: str) -> bool:
        with self._lock:
            gone = self._devices.pop(dev_id, None)
            self._sightings.pop(dev_id, None)
            if gone:
                self._save_locked()
            return gone is not None

    def list(self) -> list[dict]:
        with self._lock:
            return [self.describe(d) for d in self._devices]

    def resolve(self, query: str) -> list[dict]:
        """Best matches for "my AirPods", "phone", "Maz's iPad" (newest first ties)."""
        q = _norm(re.sub(r"^(?:my|the)\s+", "", query.strip(), flags=re.I))
        if not q:
            return []
        q_words = set(q.split())
        scored = []
        with self._lock:
            for dev in self._devices.values():
                names = [_norm(dev["name"]), *(_norm(a) for a in dev.get("aliases", []))]
                score = 0
                for n in names:
                    if n == q:
                        score = max(score, 100)
                    elif q in n or n in q:
                        score = max(score, 70)
                    elif q_words & set(n.split()):
                        score = max(score, 40)
                if dev["kind"] in q or (dev["kind"] == "earbuds" and q_words & {"earbuds", "buds", "airpods", "earphones"}):
                    score = max(score, 50)
                if dev["kind"] == "phone" and q_words & {"phone", "mobile"}:
                    score = max(score, 50)
                if score:
                    scored.append((score, dev["id"]))
        scored.sort(key=lambda row: -row[0])
        return [self.describe(dev_id) for _, dev_id in scored[:5]]

    # ---- sightings
    def report(self, observer: str, sightings: list[dict]) -> dict:
        observer = _clean(observer, 60) or "unknown"
        now = self.clock()
        matched, unassigned = 0, 0
        with self._lock:
            for raw in sightings[:200]:
                s = self._normalise(raw, observer, now)
                if s is None:
                    continue
                dev_id = self._match_locked(s)
                if dev_id:
                    buf = self._sightings.setdefault(dev_id, [])
                    buf.append(s)
                    del buf[:-MAX_SIGHTINGS_PER_DEVICE]
                    matched += 1
                else:
                    key = s.get("address") or s.get("name") or s.get("apple", {}).get("model_id", "")
                    if key:
                        prev = self._unassigned.get(key, {})
                        self._unassigned[key] = {**s, "first_at": prev.get("first_at", s["at"])}
                        unassigned += 1
            if len(self._unassigned) > MAX_UNASSIGNED:
                for key in sorted(self._unassigned, key=lambda k: self._unassigned[k]["at"])[:-MAX_UNASSIGNED]:
                    del self._unassigned[key]
            if matched:
                self._save_locked()
        return {"ok": True, "matched": matched, "unassigned": unassigned}

    def _normalise(self, raw: dict, observer: str, now: float) -> dict | None:
        if not isinstance(raw, dict):
            return None
        rssi = raw.get("rssi")
        if rssi is not None:
            try:
                rssi = int(rssi)
            except (TypeError, ValueError):
                return None
            if not -127 <= rssi <= 20:
                return None
        at = raw.get("at", now)
        try:
            at = float(at)
        except (TypeError, ValueError):
            at = now
        at = min(at, now)  # never trust a future clock
        s = {"at": at, "observer": observer, "rssi": rssi,
             "address": _clean(raw.get("address"), 17).upper(),
             "name": _clean(raw.get("name"), 60), "bonded": bool(raw.get("bonded"))}
        if raw.get("tx_power") is not None:
            try:
                s["tx_power"] = int(raw["tx_power"])
            except (TypeError, ValueError):
                pass
        if raw.get("apple_data"):
            apple = decode_apple_manufacturer(str(raw["apple_data"])[:128])
            if apple:
                s["apple"] = apple
        if raw.get("device_id"):
            s["device_id"] = _clean(raw["device_id"], 40)
        loc = raw.get("location")
        if isinstance(loc, dict):
            try:
                lat, lon = float(loc["lat"]), float(loc["lon"])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    s["location"] = {"lat": round(lat, 5), "lon": round(lon, 5),
                                     "accuracy_m": round(float(loc.get("accuracy_m", 0)), 1)}
            except (KeyError, TypeError, ValueError):
                pass
        return s

    def _match_locked(self, s: dict) -> str | None:
        if s.get("device_id") in self._devices:
            return s["device_id"]
        for dev in self._devices.values():
            bt = dev.get("providers", {}).get("bluetooth") or {}
            if s["address"] and s["address"] in bt.get("addresses", []):
                return dev["id"]
            # Names only match bonded (paired) devices: a stranger's advert can share a name.
            if s["bonded"] and s["name"] and s["name"] in bt.get("names", []):
                return dev["id"]
        return None

    def nearby_unassigned(self, now: float | None = None) -> list[dict]:
        now = self.clock() if now is None else now
        with self._lock:
            rows = [dict(v) for v in self._unassigned.values() if now - v["at"] <= RECENT_WINDOW_S]
        rows.sort(key=lambda r: -(r.get("rssi") or -200))
        for r in rows:
            r["proximity"] = proximity([r], now)
            if r.get("apple"):
                r["label"] = f"An {r['apple']['model']}-type device (identity unverified)"
            else:
                r["label"] = r.get("name") or r.get("address") or "Unnamed Bluetooth device"
        return rows

    def apple_lookalikes(self, model_id: str, now: float | None = None) -> list[dict]:
        """Unverified nearby adverts of the same Apple model (for AirPods on an iPhone)."""
        return [r for r in self.nearby_unassigned(now)
                if r.get("apple", {}).get("model_id") == model_id or (not model_id and r.get("apple"))]

    # ---- views
    def describe(self, dev_id: str, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        with self._lock:
            dev = dict(self._devices[dev_id])
            buf = list(self._sightings.get(dev_id, []))
        dev.pop("last_sighting", None)
        prox = proximity(buf, now)
        last = buf[-1] if buf else None
        located = next((s for s in reversed(buf) if s.get("location")), None)
        dev["proximity"] = prox
        dev["last_seen"] = ({"at": last["at"], "ago_s": round(now - last["at"]), "observer": last["observer"]}
                            if last else None)
        if located:
            dev["last_location"] = {**located["location"], "at": located["at"],
                                    "observer": located["observer"],
                                    "note": "Where the observing phone was when it last heard this device"}
        dev["actions"] = [a.__dict__ for a in actions_for(dev)]
        return dev


def actions_for(dev: dict) -> list[Action]:
    providers = dev.get("providers", {})
    out: list[Action] = []
    if "piblo_phone" in providers:
        out.append(Action("ring", "Ring it loudly (works on silent)", "piblo_phone", "ring"))
    if "piblo_handheld" in providers:
        out.append(Action("ring", "Beep the Piblo handheld", "piblo_handheld", "ring"))
    if "apple_findmy" in providers:
        out.append(Action("handoff_apple", "Open Apple Find My to play a sound or see it on a map",
                          "apple_findmy", "handoff", APPLE_FINDMY_URL))
    if "google_find_hub" in providers:
        out.append(Action("handoff_google", "Open Google Find Hub to ring it or see it on a map",
                          "google_find_hub", "handoff", GOOGLE_FIND_HUB_URL))
    if "bluetooth" in providers or dev.get("kind") in ("earbuds", "headphones", "tag", "watch"):
        out.append(Action("scan", "Walk around with the Piblo phone app: hotter/colder", "bluetooth", "scan"))
    return out


# --------------------------------------------------------------------------- answers
def spoken_answer(dev: dict, lookalikes: list[dict] | None = None) -> str:
    """One short, honest sentence or two for the speaker and the handheld."""
    name = dev["name"]
    prox = dev["proximity"]
    parts: list[str] = []
    if prox["state"] == "in_range":
        trend = {"closer": " and getting closer", "further": " and getting further away"}.get(prox["trend"], "")
        parts.append(f"{name}: {prox['label'].lower()}{trend}.")
    elif dev.get("last_seen"):
        parts.append(f"I last heard {name} {_ago(dev['last_seen']['ago_s'])}.")
    else:
        parts.append(f"I haven't heard {name} over Bluetooth yet.")
    if prox["state"] != "in_range" and lookalikes:
        best = lookalikes[0]
        parts.append(f"I can hear {best['label'].replace(' (identity unverified)', '')} "
                     f"{best['proximity'].get('label', '').lower()}, but I can't prove it's yours.")
    kinds = {a["provider"] for a in dev.get("actions", [])}
    if "piblo_phone" in kinds or "piblo_handheld" in kinds:
        parts.append("Say 'ring it' and I'll make it beep.")
    elif "apple_findmy" in kinds:
        parts.append("I can open Apple Find My for you to play a sound.")
    elif "google_find_hub" in kinds:
        parts.append("I can open Google Find Hub for you to ring it.")
    return " ".join(parts)


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} minutes ago"
    if seconds < 86_400 * 2:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return f"{seconds // 86_400} days ago"


def guess_kind(query: str) -> str:
    q = query.lower()
    if re.search(r"airpods|buds|earbuds|earphones|beats", q):
        return "earbuds"
    if re.search(r"headphones|headset", q):
        return "headphones"
    if re.search(r"\bphone\b|iphone|pixel|galaxy|mobile", q):
        return "phone"
    if re.search(r"ipad|tablet", q):
        return "tablet"
    if re.search(r"laptop|macbook|notebook", q):
        return "laptop"
    if re.search(r"watch", q):
        return "watch"
    if re.search(r"airtag|tile|tag|keys|wallet", q):
        return "tag"
    return "other"


def answer_unknown(query: str, nearby: list[dict]) -> dict:
    """Not in the book: suggest the right provider instead of guessing."""
    kind = guess_kind(query)
    apple = bool(re.search(r"airpods|iphone|ipad|macbook|apple|airtag|beats", query, re.I))
    actions = []
    if apple:
        actions.append(Action("handoff_apple", "Open Apple Find My", "apple_findmy", "handoff", APPLE_FINDMY_URL).__dict__)
    elif kind in ("phone", "tablet", "earbuds", "watch", "tag"):
        actions.append(Action("handoff_google", "Open Google Find Hub", "google_find_hub", "handoff",
                              GOOGLE_FIND_HUB_URL).__dict__)
    candidates = [n for n in nearby if (apple and n.get("apple")) or
                  (n.get("name") and set(_norm(query).split()) & set(_norm(n["name"]).split()))]
    reply = f"I don't know your {query.strip()} yet."
    if candidates:
        reply += f" I can hear {candidates[0]['label'].replace(' (identity unverified)', '')} nearby."
    if actions:
        reply += f" I can open {'Apple Find My' if apple else 'Google Find Hub'} for you."
    reply += " Add it in the Piblo app under Find devices so I can track it next time."
    return {"found": False, "query": query, "kind_guess": kind, "candidates": candidates[:5],
            "actions": actions, "reply": reply}


def range_text(prox: dict) -> str:
    if prox.get("state") != "in_range":
        return ""
    lo, hi = prox["range_m"]
    return f"roughly {lo:g}-{hi:g} m ({prox['confidence']} confidence)"


__all__ = [
    "DeviceBook", "decode_apple_manufacturer", "proximity", "rssi_range_m", "spoken_answer",
    "answer_unknown", "actions_for", "APPLE_FINDMY_URL", "GOOGLE_FIND_HUB_URL",
]
