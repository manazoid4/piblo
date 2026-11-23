"""End to end through the real Core app: /finddevices, /assist and the spoken /turn path."""
from test_app import FakeModels, client

H = {"Authorization": "Bearer test-token-that-is-not-default"}


def test_find_my_phone_over_turn_and_assist(tmp_path):
    models = FakeModels()
    api = client(models=models, work_dir=tmp_path)
    assert api.post("/finddevices/devices", headers=H, json={
        "id": "pixel", "name": "Pixel phone", "kind": "phone", "aliases": ["phone"],
        "providers": {"piblo_phone": {}}}).status_code == 200
    api.post("/finddevices/sightings", headers=H, json={"observer": "android:pixel", "sightings": [
        {"device_id": "pixel", "rssi": -45}]})

    sid = api.post("/session/start", headers=H).json()["session_id"]
    first = api.post("/turn/text", headers=H, json={"session_id": sid, "route": "auto",
                                                     "text": "Piblo, find my phone"}).json()
    assert first["provider"] == "piblo-assist"
    assert "very close" in first["reply"].lower()

    ring = api.post("/turn/text", headers=H, json={"session_id": sid, "route": "auto", "text": "ring it"}).json()
    assert ring["provider"] == "piblo-assist" and "Ringing" in ring["reply"]
    alerts = api.get("/buddy/alerts", headers=H).json()["alerts"]
    assert any(a["source"] == "find_phone" for a in alerts)

    # Ordinary chat is untouched: "yes" with nothing pending goes to the model.
    other = api.post("/session/start", headers=H).json()["session_id"]
    chat = api.post("/turn/text", headers=H, json={"session_id": other, "route": "auto", "text": "yes"}).json()
    assert chat["provider"] != "piblo-assist"

    run = api.post("/assist", headers=H, json={"text": "where are my phone", "wait": 3}).json()["run"]
    assert run["state"] == "done" and run["capability"] == "devices.find"
    metrics = api.get("/assist/metrics", headers=H).json()
    assert metrics["runs"] >= 3 and metrics["success_rate"] == 1.0

    caps = {c["id"]: c for c in api.get("/capabilities", headers=H).json()["capabilities"]}
    assert caps["fix.app.end_task"]["risk"] == "sensitive"
    assert caps["devices.find"]["risk"] == "read"
    blocked = api.post("/capabilities/fix.disk.clean_temp/execute", headers=H, json={"args": {}}).json()
    assert blocked["blocked"] and blocked["needs_approval"]


def test_sort_this_out_and_carry_on_answer_without_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MAZ_BUDDY_DB", str(tmp_path / "buddy.db"))  # agent tasks live in the store
    api = client(work_dir=tmp_path)
    run = api.post("/assist", headers=H, json={"text": "carry on where I left off", "wait": 3}).json()["run"]
    assert run["state"] == "done" and run["capability"] == "resume.packet"
    api.post("/buddy/task", headers=H, json={"id": "t1", "title": "Fix login bug", "status": "running",
                                              "agent": "PibloCraft", "progress": 40})
    run = api.post("/assist", headers=H, json={"text": "where was I?", "wait": 3}).json()["run"]
    assert "Fix login bug" in run["reply"]
    run = api.post("/assist", headers=H, json={"text": "sort this out", "wait": 10}).json()["run"]
    assert run["capability"] == "troubleshoot.diagnose" and run["state"] == "done"
    assert run["reply"]
    assert api.post("/assist", json={"text": "find my phone"}).status_code == 401
