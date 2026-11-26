""""Piblo, sort this out": look, diagnose, fix (with approval), verify.

Evidence comes from what is actually on the PC: the foreground window title,
the screen (capture + OCR when an engine is installed), and cheap
deterministic probes (disk, memory, DNS, listening ports, hung windows).
A rule table maps evidence to a diagnosis and, where a safe and reversible
fix exists, to a registered fix capability. Fixes are "sensitive" in the
registry, so they only run after the user approves on their device, and each
rule has a verify step that re-runs the probe that found the problem.

A model is only asked when no rule matches, and only with the OCR text, so
the common problems cost nothing and work offline.
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import private_mode

IS_WINDOWS = platform.system() == "Windows"
DISK_LOW_FRACTION = 0.05
MEMORY_HIGH_PERCENT = 92.0
TEMP_MIN_AGE_S = 24 * 3600


# --------------------------------------------------------------------------- evidence
def foreground_title() -> str:
    if not IS_WINDOWS:
        return ""
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def _reachable(url: str, timeout: float) -> bool:
    """Any HTTP answer means the network path works. Private mode: not checked, reported fine."""
    if private_mode.enabled():
        return True
    try:
        with private_mode.client(timeout=timeout) as http:
            http.head(url, follow_redirects=False)
        return True
    except Exception:
        return False


def probe_dns(host: str = "www.google.com", timeout: float = 2.0) -> bool:
    """A public name resolves and answers (needs working DNS)."""
    return _reachable(f"https://{host}/generate_204", timeout)


def probe_tcp(host: str = "1.1.1.1", timeout: float = 2.0) -> bool:
    """An IP address answers (internet works even if DNS does not)."""
    return _reachable(f"https://{host}/", timeout)


def probe_disk(path: str | None = None) -> dict:
    target = path or (os.environ.get("SystemDrive", "C:") + "\\" if IS_WINDOWS else "/")
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return {"path": target, "ok": True, "unknown": True}
    free = usage.free / usage.total if usage.total else 1.0
    return {"path": target, "free_gb": round(usage.free / 1e9, 1), "free_fraction": round(free, 3),
            "ok": free >= DISK_LOW_FRACTION}


def probe_memory() -> dict:
    try:
        import psutil
    except ImportError:
        return {"ok": True, "unknown": True}
    vm = psutil.virtual_memory()
    top = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            top.append((proc.info["memory_info"].rss, proc.info["pid"], proc.info["name"]))
        except (psutil.Error, AttributeError, TypeError):
            continue
    top.sort(reverse=True)
    return {"percent": vm.percent, "ok": vm.percent < MEMORY_HIGH_PERCENT,
            "top": [{"pid": pid, "name": name, "mb": round(rss / 1e6)} for rss, pid, name in top[:5]]}


def port_owner(port: int) -> dict | None:
    try:
        import psutil
    except ImportError:
        return None
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError):
        return None
    for c in conns:
        if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
            try:
                return {"pid": c.pid, "name": psutil.Process(c.pid).name(), "port": port}
            except psutil.Error:
                return {"pid": c.pid, "name": "", "port": port}
    return None


def hung_windows() -> list[str]:
    """Windows reports hung GUI apps as 'Not Responding' in tasklist."""
    if not IS_WINDOWS:
        return []
    private_mode.guard_subprocess("tasklist", local=True)
    try:
        out = subprocess.run(["tasklist", "/fi", "STATUS eq NOT RESPONDING", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=8,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in out.splitlines():
        if line.startswith('"'):
            names.append(line.split('","')[0].strip('"'))
    return names


@dataclass
class Evidence:
    title: str = ""
    text: str = ""
    ocr_available: bool = False
    screenshot: str = ""
    dns_ok: bool = True
    internet_ok: bool = True
    disk: dict = field(default_factory=dict)
    memory: dict = field(default_factory=dict)
    hung: list[str] = field(default_factory=list)
    gathered_ms: int = 0

    @property
    def haystack(self) -> str:
        return f"{self.title}\n{self.text}"

    def public(self) -> dict:
        return {"window_title": self.title, "screen_text_chars": len(self.text),
                "screen_text_excerpt": self.text[:400], "ocr_available": self.ocr_available,
                "screenshot": self.screenshot, "dns_ok": self.dns_ok, "internet_ok": self.internet_ok,
                "disk": self.disk, "memory": {k: v for k, v in self.memory.items() if k != "top"},
                "hung_apps": self.hung, "gathered_ms": self.gathered_ms}


class Gatherer:
    """Collects evidence. Every source is injectable so tests use no real PC."""

    def __init__(self, perception=None, *, title: Callable[[], str] = foreground_title,
                 dns: Callable[[], bool] = probe_dns, tcp: Callable[[], bool] = probe_tcp,
                 disk: Callable[[], dict] = probe_disk, memory: Callable[[], dict] = probe_memory,
                 hung: Callable[[], list[str]] = hung_windows) -> None:
        self.perception = perception
        self.title, self.dns, self.tcp, self.disk, self.memory, self.hung = title, dns, tcp, disk, memory, hung

    def gather(self, screen_text: str | None = None, look: bool = True) -> Evidence:
        started = time.perf_counter()
        ev = Evidence(title=self.title() or "")
        if screen_text is not None:
            ev.text, ev.ocr_available = screen_text, True
        elif look and self.perception is not None:
            try:
                snap = self.perception.capture_screen()
                ev.screenshot = snap.path
                ocr = self.perception.ocr_text(snap.path)
                ev.ocr_available = bool(ocr.get("available"))
                ev.text = str(ocr.get("text", ""))[:20_000]
            except Exception:
                pass
        ev.dns_ok = self.dns()
        ev.internet_ok = ev.dns_ok or self.tcp()
        ev.disk = self.disk()
        ev.memory = self.memory()
        ev.hung = self.hung()
        ev.gathered_ms = round((time.perf_counter() - started) * 1000)
        return ev


# --------------------------------------------------------------------------- rules
@dataclass
class Finding:
    rule: str
    summary: str
    advice: str
    fix: dict | None = None      # {"capability": id, "args": {...}, "label": str}
    verify: dict | None = None   # {"probe": name, ...}
    confidence: float = 0.8

    def public(self) -> dict:
        return {"rule": self.rule, "summary": self.summary, "advice": self.advice,
                "fix": self.fix, "verify": self.verify, "confidence": self.confidence}


def _has(text: str, *patterns: str) -> re.Match | None:
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m
    return None


def diagnose(ev: Evidence) -> list[Finding]:
    text = ev.haystack
    found: list[Finding] = []

    hung_match = _has(text, r"^(.{1,80}?)\s*\(Not Responding\)", r"(\S[^\n]{0,60}?) is not responding")
    if ev.hung or hung_match:
        app = (ev.hung[0] if ev.hung else hung_match.group(1).strip())
        process = app if app.lower().endswith(".exe") else ""
        found.append(Finding(
            "app_not_responding", f"{app} has stopped responding.",
            "Give it 10 seconds; if it stays frozen, end it. Unsaved work in it may be lost.",
            fix={"capability": "fix.app.end_task", "args": {"process": process or app},
                 "label": f"End {app}"} if process or ev.hung else None,
            verify={"probe": "not_hung", "process": process or app}, confidence=0.9))

    port = _has(text, r"EADDRINUSE[^\n]*?:(\d{2,5})", r"address already in use[^\n]*?:(\d{2,5})",
                r"port (\d{2,5}) is already in use", r"Only one usage of each socket address[^\n]*?:(\d{2,5})")
    if port:
        number = int(port.group(1))
        owner = port_owner(number)
        who = f"{owner['name']} (PID {owner['pid']})" if owner else "another program"
        found.append(Finding(
            "port_in_use", f"Port {number} is already taken by {who}.",
            "Stop the old copy of the server, or start this one on another port.",
            fix={"capability": "fix.app.end_task", "args": {"pid": owner["pid"]},
                 "label": f"Stop {who}"} if owner else None,
            verify={"probe": "port_free", "port": number}, confidence=0.9))

    module = _has(text, r"ModuleNotFoundError: No module named ['\"]([\w.\-]+)['\"]")
    if module:
        name = module.group(1).split(".")[0]
        found.append(Finding(
            "python_module_missing", f"Python can't find the '{name}' package.",
            f"Install it into the same environment: python -m pip install {name}  "
            "(package names sometimes differ from import names).", confidence=0.85))

    if _has(text, r"Cannot find module ['\"]([^'\"]+)['\"]", r"npm ERR! missing"):
        found.append(Finding("node_module_missing", "Node can't find an installed package.",
                             "Run npm install in the project folder, then try again.", confidence=0.8))

    cmd = _has(text, r"['\"]?([\w.\-]+)['\"]? is not recognized as an internal or external command",
               r"([\w.\-]+): command not found")
    if cmd:
        found.append(Finding("command_missing", f"The command '{cmd.group(1)}' isn't installed or isn't on PATH.",
                             "Install it, or open a new terminal if you installed it a moment ago.",
                             confidence=0.85))

    if _has(text, r"NET::ERR_CERT_DATE_INVALID", r"your clock is (?:ahead|behind)"):
        found.append(Finding("clock_wrong", "The PC clock looks wrong, so secure sites are refused.",
                             "Resync the clock (Settings > Time > Sync now).",
                             fix={"capability": "fix.time.resync", "args": {}, "label": "Resync the clock"}
                             if IS_WINDOWS else None, confidence=0.85))

    offline_text = _has(text, r"ERR_INTERNET_DISCONNECTED", r"DNS_PROBE_\w+", r"No internet",
                        r"You're not connected", r"Hmm[,.]? we can[’']t reach this page",
                        r"ERR_NAME_NOT_RESOLVED", r"Server not found")
    if not ev.internet_ok:
        found.append(Finding("offline", "The PC has no internet connection right now.",
                             "Check Wi-Fi is on and connected; restart the router if other devices are offline too.",
                             verify={"probe": "internet"}, confidence=0.95))
    elif not ev.dns_ok or (offline_text and "DNS" in offline_text.group(0).upper()):
        found.append(Finding("dns", "The internet works but names aren't resolving (DNS).",
                             "Flushing the DNS cache usually fixes this.",
                             fix={"capability": "fix.network.flush_dns", "args": {}, "label": "Flush DNS"}
                             if IS_WINDOWS else None,
                             verify={"probe": "dns"}, confidence=0.85))
    elif offline_text:
        found.append(Finding("page_unreachable", "That page didn't load, but this PC is online.",
                             "The site may be down or the address mistyped; try reloading in a minute.",
                             confidence=0.6))

    disk_text = _has(text, r"low disk space", r"not enough (?:disk )?space", r"disk (?:is )?full",
                     r"ENOSPC", r"There is not enough space on the disk")
    if (ev.disk and not ev.disk.get("ok", True)) or disk_text:
        free = ev.disk.get("free_gb")
        found.append(Finding(
            "disk_low", "The disk is almost full" + (f" ({free} GB free)." if free is not None else "."),
            "Clearing temporary files is safe; then empty the Recycle Bin and Downloads.",
            fix={"capability": "fix.disk.clean_temp", "args": {}, "label": "Clear old temporary files"},
            verify={"probe": "disk_freed", "before_gb": free}, confidence=0.9))

    if ev.memory and not ev.memory.get("ok", True):
        top = ev.memory.get("top", [])
        hog = top[0] if top else None
        found.append(Finding(
            "memory_high", f"Memory is {ev.memory.get('percent')}% used"
            + (f"; {hog['name']} is using {hog['mb']} MB." if hog else "."),
            "Close apps you aren't using; browsers with many tabs are the usual cause.",
            fix={"capability": "fix.app.end_task", "args": {"pid": hog["pid"]}, "label": f"End {hog['name']}"}
            if hog else None, confidence=0.7))

    if _has(text, r"Access (?:is )?denied", r"Permission denied", r"EACCES"):
        found.append(Finding("access_denied", "Something was blocked by file or admin permissions.",
                             "Check the file isn't open elsewhere; for system folders, run the tool as administrator.",
                             confidence=0.6))

    if _has(text, r"restart (?:is )?required", r"Restart to (?:update|finish)", r"pending restart"):
        found.append(Finding("restart_pending", "An update is waiting for a restart.",
                             "Save your work and restart when it suits you. Piblo won't restart for you.",
                             confidence=0.8))
    found.sort(key=lambda f: -f.confidence)
    return found


def verify(spec: dict | None, gatherer: Gatherer) -> dict:
    """Re-run the probe that found the problem."""
    if not spec:
        return {"checked": False}
    probe = spec.get("probe")
    if probe == "internet":
        ok = gatherer.dns() or gatherer.tcp()
    elif probe == "dns":
        ok = gatherer.dns()
    elif probe == "port_free":
        ok = port_owner(int(spec["port"])) is None
    elif probe == "not_hung":
        ok = str(spec.get("process", "")).lower() not in [h.lower() for h in gatherer.hung()]
    elif probe == "disk_freed":
        after = gatherer.disk().get("free_gb")
        before = spec.get("before_gb")
        ok = after is not None and (before is None or after >= before)
        return {"checked": True, "ok": ok, "free_gb": after}
    else:
        return {"checked": False}
    return {"checked": True, "ok": ok}


# --------------------------------------------------------------------------- fixes
def end_task(args: dict) -> dict:
    import psutil
    pid = args.get("pid")
    name = str(args.get("process", "")).strip()
    targets = []
    if pid:
        targets = [psutil.Process(int(pid))]
    elif name:
        targets = [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == name.lower()]
    if not targets:
        raise ValueError("process_not_found")
    protected = {"system", "csrss.exe", "wininit.exe", "winlogon.exe", "lsass.exe", "services.exe",
                 "smss.exe", "explorer.exe", "svchost.exe", "dwm.exe"}
    ended = []
    for proc in targets[:5]:
        pname = proc.name()
        if pname.lower() in protected or proc.pid == os.getpid():
            raise PermissionError(f"refusing_to_end_{pname}")
        proc.terminate()
        ended.append({"pid": proc.pid, "name": pname})
    psutil.wait_procs(targets[:5], timeout=5)
    return {"ended": ended, "reply": "Ended " + ", ".join(e["name"] for e in ended) + "."}


def flush_dns(args: dict) -> dict:
    if not IS_WINDOWS:
        raise NotImplementedError("flush_dns_windows_only")
    private_mode.guard_subprocess("ipconfig", local=True)
    out = subprocess.run(["ipconfig", "/flushdns"], capture_output=True, text=True, timeout=15,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0:
        raise RuntimeError("flush_dns_failed")
    return {"reply": "Flushed the DNS cache."}


def resync_time(args: dict) -> dict:
    if not IS_WINDOWS:
        raise NotImplementedError("resync_windows_only")
    private_mode.guard_subprocess("w32tm")  # contacts a time server, so blocked in private mode
    out = subprocess.run(["w32tm", "/resync"], capture_output=True, text=True, timeout=30,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if out.returncode != 0:
        raise RuntimeError("time_resync_failed: run as administrator or use Settings > Time > Sync now")
    return {"reply": "Clock resynced."}


def clean_temp(args: dict, root: str | None = None, min_age_s: float = TEMP_MIN_AGE_S) -> dict:
    """Delete files older than a day in the user's temp folder only. Skips anything in use."""
    base = Path(root or tempfile.gettempdir()).resolve()
    home = Path.home().resolve()
    if root is None and home not in base.parents and not IS_WINDOWS:
        # On Linux/mac the system temp is shared: only clean when pointed at a user folder.
        raise PermissionError("temp_folder_not_user_owned")
    now = time.time()
    freed, removed, skipped = 0, 0, 0
    for folder, dirs, files in os.walk(base, topdown=False):
        for name in files:
            path = Path(folder) / name
            try:
                st = path.lstat()
                if path.is_symlink() or now - st.st_mtime < min_age_s:
                    continue
                path.unlink()
                freed += st.st_size
                removed += 1
            except OSError:
                skipped += 1
        if Path(folder) != base:
            try:
                Path(folder).rmdir()
            except OSError:
                pass
    return {"removed_files": removed, "skipped_in_use": skipped, "freed_mb": round(freed / 1e6, 1),
            "reply": f"Cleared {round(freed / 1e6)} MB of old temporary files."}


# --------------------------------------------------------------------------- service
class Troubleshooter:
    def __init__(self, gatherer: Gatherer, explain: Callable[[str], str] | None = None) -> None:
        self.gatherer = gatherer
        self.explain = explain
        self._last: dict[str, Finding] = {}

    def diagnose(self, screen_text: str | None = None, look: bool = True, allow_model: bool = True) -> dict:
        ev = self.gatherer.gather(screen_text, look)
        findings = diagnose(ev)
        self._last = {f.rule: f for f in findings}
        result: dict[str, Any] = {"evidence": ev.public(), "findings": [f.public() for f in findings],
                                  "used_model": False}
        if findings:
            top = findings[0]
            result["reply"] = top.summary + " " + top.advice
            if top.fix:
                result["reply"] += f" Want me to {top.fix['label'].lower()}? I'll ask on your Piblo first."
                result["next"] = {"capability": top.fix["capability"], "args": top.fix["args"],
                                  "label": top.fix["label"],
                                  "verify": {"capability": "troubleshoot.verify", "args": {"rule": top.rule}}}
        elif allow_model and self.explain and ev.text.strip():
            try:
                result["reply"] = self.explain(ev.text[:4000])
                result["used_model"] = True
            except Exception:
                result["reply"] = "I can see the screen but don't recognise the problem, and no model is available."
        elif not ev.ocr_available and look:
            result["reply"] = ("Your PC checks look fine (internet, disk, memory, no frozen apps). "
                               "I can't read the screen yet because no OCR engine is installed.")
        else:
            result["reply"] = "Everything I can check looks fine: internet, disk, memory and no frozen apps."
        return result

    def verify(self, rule: str) -> dict:
        finding = self._last.get(rule)
        if not finding:
            return {"checked": False, "reply": "Nothing to check; run a diagnosis first."}
        out = verify(finding.verify, self.gatherer)
        if out.get("checked"):
            out["reply"] = "Fixed: checked again and it's clear." if out["ok"] else "Still not right after the fix."
        else:
            out["reply"] = "Done. I don't have an automatic check for this one; have a look."
        return out


def install_troubleshoot(registry, *, perception=None, explain: Callable[[str], str] | None = None,
                         gatherer: Gatherer | None = None) -> Troubleshooter:
    from .universal_registry import Capability

    ts = Troubleshooter(gatherer or Gatherer(perception), explain)
    obj = {"type": "object"}

    def _diag(args, context):
        return ts.diagnose(args.get("screen_text"), bool(args.get("look", True)), bool(args.get("allow_model", True)))

    registry.register(Capability(
        id="troubleshoot.diagnose", name="Sort this out", category="troubleshooting", risk="read",
        description="Look at the screen and run PC health checks, then explain the problem and the fix.",
        input_schema={"type": "object", "properties": {"screen_text": {"type": "string"},
                                                       "look": {"type": "boolean"}}},
        output_schema=obj, required_permissions=["screen_read"], handler=_diag))
    registry.register(Capability(
        id="troubleshoot.verify", name="Check the fix", category="troubleshooting", risk="read",
        description="Re-run the check that found a problem.",
        input_schema={"type": "object", "properties": {"rule": {"type": "string"}}, "required": ["rule"]},
        output_schema=obj, required_permissions=["screen_read"],
        handler=lambda args, context: ts.verify(str(args.get("rule", "")))))
    for cap_id, name, handler in (
        ("fix.app.end_task", "End a frozen app", lambda a, context: end_task(a)),
        ("fix.network.flush_dns", "Flush DNS", lambda a, context: flush_dns(a)),
        ("fix.time.resync", "Resync clock", lambda a, context: resync_time(a)),
        ("fix.disk.clean_temp", "Clear old temp files", lambda a, context: clean_temp(a)),
    ):
        registry.register(Capability(
            id=cap_id, name=name, category="troubleshooting", risk="sensitive",
            description=f"{name} (needs approval on the Piblo device).",
            input_schema={"type": "object"}, output_schema=obj,
            required_permissions=["pc_fix"], handler=handler))
    return ts
