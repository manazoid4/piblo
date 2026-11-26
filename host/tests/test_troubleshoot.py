import asyncio
import os
import tempfile
import time
from pathlib import Path

from mazhost import troubleshoot as ts
from mazhost.universal_registry import EventBus, PermissionPolicy, UniversalCapabilityRegistry


def gatherer(dns=True, tcp=True, disk=None, memory=None, hung=None, title=""):
    return ts.Gatherer(None, title=lambda: title, dns=lambda: dns, tcp=lambda: tcp,
                       disk=lambda: disk or {"ok": True, "free_gb": 100.0},
                       memory=lambda: memory or {"ok": True, "percent": 40},
                       hung=lambda: list(hung or []))


def rules(text="", **kw):
    ev = gatherer(**kw).gather(screen_text=text, look=False)
    return [f.rule for f in ts.diagnose(ev)]


def test_healthy_pc_says_so_without_model():
    t = ts.Troubleshooter(gatherer(), explain=None)
    out = t.diagnose(screen_text="", look=False)
    assert out["findings"] == [] and "looks fine" in out["reply"] and out["used_model"] is False


def test_rules_from_screen_text():
    assert "python_module_missing" in rules("ModuleNotFoundError: No module named 'httpx'")
    assert "port_in_use" in rules("Error: listen EADDRINUSE: address already in use :::3000")
    assert "command_missing" in rules("'node' is not recognized as an internal or external command")
    assert "clock_wrong" in rules("NET::ERR_CERT_DATE_INVALID")
    assert "access_denied" in rules("PermissionError: [Errno 13] Permission denied")
    assert "restart_pending" in rules("Restart required to finish installing updates")
    assert "app_not_responding" in rules(title="Untitled - Notepad (Not Responding)")
    assert "page_unreachable" in rules("Hmm, we can't reach this page.")


def test_rules_from_probes_and_order():
    assert rules(dns=False, tcp=False)[0] == "offline"
    assert "dns" in rules(dns=False, tcp=True)
    assert "disk_low" in rules(disk={"ok": False, "free_gb": 1.2})
    assert "memory_high" in rules(memory={"ok": False, "percent": 97, "top": [{"pid": 4242, "name": "chrome.exe", "mb": 3000}]})
    assert rules(hung=["excel.exe"])[0] == "app_not_responding"


def test_fix_is_offered_as_next_step_with_verify():
    t = ts.Troubleshooter(gatherer(hung=["excel.exe"]))
    out = t.diagnose(screen_text="", look=False)
    assert out["next"]["capability"] == "fix.app.end_task"
    assert out["next"]["args"] == {"process": "excel.exe"}
    assert out["next"]["verify"]["args"] == {"rule": "app_not_responding"}
    assert "ask on your Piblo" in out["reply"]


def test_verify_rechecks_probe():
    state = {"dns": False}
    g = ts.Gatherer(None, title=lambda: "", dns=lambda: state["dns"], tcp=lambda: True,
                    disk=lambda: {"ok": True}, memory=lambda: {"ok": True}, hung=lambda: [])
    t = ts.Troubleshooter(g)
    t.diagnose(screen_text="", look=False)
    assert t.verify("dns")["ok"] is False
    state["dns"] = True
    assert t.verify("dns")["ok"] is True
    assert t.verify("unknown")["checked"] is False


def test_model_only_when_no_rule_matches():
    calls = []
    t = ts.Troubleshooter(gatherer(), explain=lambda text: calls.append(text) or "The printer is offline.")
    out = t.diagnose(screen_text="Printer HP-123 offline queue paused", look=False)
    assert out["used_model"] is True and calls
    calls.clear()
    t.diagnose(screen_text="ModuleNotFoundError: No module named 'x'", look=False)
    assert calls == []


def test_model_failure_is_graceful():
    def boom(_):
        raise RuntimeError("offline")
    t = ts.Troubleshooter(gatherer(), explain=boom)
    out = t.diagnose(screen_text="something odd", look=False)
    assert "no model is available" in out["reply"]


def test_clean_temp_only_removes_old_files():
    root = Path(tempfile.mkdtemp())
    old, new = root / "old.tmp", root / "sub" / "new.tmp"
    new.parent.mkdir()
    old.write_bytes(b"x" * 1000)
    new.write_bytes(b"y")
    past = time.time() - 3 * 86400
    os.utime(old, (past, past))
    out = ts.clean_temp({}, root=str(root))
    assert out["removed_files"] == 1 and not old.exists() and new.exists()


def test_fixes_are_sensitive_and_need_approval():
    reg = UniversalCapabilityRegistry(EventBus(), PermissionPolicy(None))
    ts.install_troubleshoot(reg, gatherer=gatherer())
    for cap in ("fix.app.end_task", "fix.network.flush_dns", "fix.disk.clean_temp", "fix.time.resync"):
        assert reg.get(cap).risk == "sensitive"
        out = asyncio.run(reg.execute(cap, {}, {"origin": "user"}))
        assert out["blocked"] and out["needs_approval"]
    out = asyncio.run(reg.execute("troubleshoot.diagnose", {"screen_text": "", "look": False}, {"origin": "assist"}))
    assert out["success"]


def test_end_task_refuses_windows_processes(monkeypatch):
    class P:
        pid = 99999

        def name(self):
            return "explorer.exe"

    import psutil
    monkeypatch.setattr(psutil, "Process", lambda pid: P())
    try:
        ts.end_task({"pid": 99999})
    except PermissionError as error:
        assert "refusing_to_end" in str(error)
    else:
        raise AssertionError("should refuse")
