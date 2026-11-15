from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import subprocess
import wave
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from mazhost import dumps
from mazhost.app import create_app
from mazhost.config import Settings
import nodinbox

STRUCT = {"summary": "Plan the launch", "ideas": ["a idea"], "actions": ["ship it"], "questions": ["when?"]}


def test_save_round_trip_and_index(tmp_path):
    meta = dumps.save_dump(tmp_path, "full raw words", STRUCT, source="device", project="p")
    got = dumps.get_dump(tmp_path, meta["id"])
    assert got["status"] == "new" and got["source"] == "device" and got["project"] == "p"
    assert got["summary"] == "Plan the launch"
    assert "- [ ] ship it" in got["body"] and "## Transcript\nfull raw words" in got["body"]
    index = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    assert meta["id"] in index and "### p" in index and "nodinbox.py claim" in index
    assert [m["id"] for m in dumps.list_dumps(tmp_path, status="new", project="p")] == [meta["id"]]


def test_keyword_assignment_and_unassigned(tmp_path):
    (tmp_path / "projects.json").write_text(
        json.dumps([{"name": "alpha", "keywords": ["Widget"]}, {"name": "beta", "keywords": ["gizmo"]}])
    )
    assert dumps.assign_project(tmp_path, "fix the WIDGET and widget", "") == "alpha"
    assert dumps.assign_project(tmp_path, "nothing relevant", "") == "unassigned"
    assert dumps.assign_project(tmp_path, "nothing", "", llm=lambda s, u: "Beta.") == "beta"
    assert dumps.assign_project(tmp_path, "nothing", "", llm=lambda s, u: "made-up") == "unassigned"

    def boom(s, u):
        raise RuntimeError("down")

    assert dumps.assign_project(tmp_path, "nothing", "", llm=boom) == "unassigned"


def test_starter_projects_created(tmp_path):
    assert dumps.assign_project(tmp_path, "hello", "") == "unassigned"
    assert (tmp_path / "projects.json").is_file()


def test_double_claim_rejected_then_done(tmp_path):
    dump_id = dumps.save_dump(tmp_path, "t", STRUCT, source="api")["id"]
    dumps.claim_dump(tmp_path, dump_id, "codex")
    dumps.claim_dump(tmp_path, dump_id, "codex")  # same agent is idempotent
    with pytest.raises(dumps.DumpError, match="already_claimed_by:codex"):
        dumps.claim_dump(tmp_path, dump_id, "hermes")
    with pytest.raises(dumps.DumpError):
        dumps.finish_dump(tmp_path, dump_id, "hermes")
    dumps.finish_dump(tmp_path, dump_id, "codex", "did it")
    got = dumps.get_dump(tmp_path, dump_id)
    assert got["status"] == "done" and "did it" in got["body"]
    assert dump_id not in (tmp_path / "INDEX.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", "..", "a.b", "", "x" * 200])
def test_id_path_traversal_rejected(tmp_path, bad):
    with pytest.raises(dumps.DumpError, match="invalid_dump_id"):
        dumps.get_dump(tmp_path, bad)


def test_cli_claim_exit_code(tmp_path):
    dump_id = dumps.save_dump(tmp_path, "t", STRUCT, source="api")["id"]
    base = ["--dir", str(tmp_path)]
    assert nodinbox.main(base + ["claim", dump_id, "--agent", "a"]) == 0
    assert nodinbox.main(base + ["claim", dump_id, "--agent", "b"]) == 1
    assert nodinbox.main(base + ["add", "I need to buy milk."]) == 0
    assert nodinbox.main(base + ["list"]) == 0


# ---- routes -------------------------------------------------------------

class Stt:
    def available(self):
        return True

    def transcribe(self, _path):
        return "spoken words for the dump."


class Models:
    def __init__(self, fail=False):
        self.fail = fail

    def status(self):
        return {}

    def chat(self, messages, route):
        if self.fail == "route":  # what the real router raises with no model at all
            from mazhost.errors import ErrorCode, RouteError
            raise RouteError(ErrorCode.ROUTE_UNAVAILABLE, "degraded")
        if self.fail:
            raise RuntimeError("llm down")
        return json.dumps(STRUCT), "local"


def make(tmp_path, models=None):
    cfg = Settings(token="test-token-that-is-not-default", _env_file=None, dumps_dir=str(tmp_path / "inbox"))
    return TestClient(create_app(cfg, stt=Stt(), models=models or Models())), tmp_path / "inbox"


AUTH = {"Authorization": "Bearer test-token-that-is-not-default"}


def wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(bytes(3200))
    return buf.getvalue()


def post_dump(client):
    return client.post("/braindump", headers=AUTH, files={"audio": ("a.wav", wav(), "audio/wav")})


def test_braindump_returns_dump_id(tmp_path):
    client, inbox = make(tmp_path)
    r = post_dump(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"] == "Plan the launch" and body["provider"] == "local"
    assert body["project"] == "unassigned"
    assert "spoken words" in dumps.get_dump(inbox, body["dump_id"])["body"]


def test_braindump_raw_returns_dump_id(tmp_path):
    client, _ = make(tmp_path)
    r = client.post("/braindump/raw", headers={**AUTH, "Content-Type": "audio/wav"}, content=wav())
    assert r.status_code == 200, r.text
    assert r.json()["dump_id"]


def test_braindump_save_failure_does_not_break_reply(tmp_path, monkeypatch):
    client, _ = make(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(dumps, "save_dump", boom)
    r = post_dump(client)
    assert r.status_code == 200 and r.json()["dump_id"] is None


@pytest.mark.parametrize("fail", [True, "route"])
def test_llm_failure_still_saves_transcript(tmp_path, fail):
    client, inbox = make(tmp_path, Models(fail=fail))
    r = post_dump(client)  # no brain must not look like "Core is off" on the device
    assert r.status_code == 200, r.text
    assert r.json()["provider"].startswith("none") and r.json()["dump_id"]
    [m] = dumps.list_dumps(inbox)
    assert "spoken words for the dump." in dumps.get_dump(inbox, m["id"])["body"]


def test_dump_routes(tmp_path):
    client, _ = make(tmp_path)
    assert client.get("/dumps").status_code == 401
    made = client.post("/dumps", headers=AUTH, json={"text": "I need to call the bank."}).json()
    dump_id = made["dump_id"]
    assert client.get("/dumps", headers=AUTH, params={"status": "new"}).json()["dumps"][0]["id"] == dump_id
    assert client.get(f"/dumps/{dump_id}", headers=AUTH).json()["status"] == "new"
    assert client.post(f"/dumps/{dump_id}/claim", headers=AUTH, json={"agent": "a"}).status_code == 200
    assert client.post(f"/dumps/{dump_id}/claim", headers=AUTH, json={"agent": "b"}).status_code == 409
    assert client.post(f"/dumps/{dump_id}/assign", headers=AUTH, json={"project": "personal"}).json()["project"] == "personal"
    assert client.post(f"/dumps/{dump_id}/done", headers=AUTH, json={"agent": "a", "note": "ok"}).json()["status"] == "done"
    assert client.get("/dumps/nope", headers=AUTH).status_code == 404
    assert client.get("/dumps/..%2Fprojects", headers=AUTH).status_code in (400, 404)


def test_get_dump_returns_parsed_sections(tmp_path):
    structured = {"summary": "s", "actions": ["do a"], "ideas": ["idea ## b"], "questions": []}
    meta = dumps.save_dump(tmp_path, "line one\n## not a heading\nline two", structured, source="api")
    dumps.finish_dump(tmp_path, meta["id"], "tester", "note")
    got = dumps.get_dump(tmp_path, meta["id"])
    assert got["actions"] == ["do a"]
    assert got["ideas"] == ["idea ## b"]
    assert got["questions"] == []
    assert got["transcript"] == "line one\n## not a heading\nline two"


def test_recall_sources_survive_new_app_instance(tmp_path):
    client, inbox = make(tmp_path)
    ids = [dumps.save_dump(inbox, text, None, source="web")["id"] for text in
           ("Amberkite: bring the blue folder.", "Amberkite: book a quiet room.")]
    restarted, _ = make(tmp_path)
    listed = restarted.get("/dumps", headers=AUTH).json()["dumps"]
    assert {d["id"] for d in listed} == set(ids)
    for meta in listed:
        original = restarted.get(f"/dumps/{meta['id']}", headers=AUTH).json()
        assert original["created"] == meta["created"]
        assert "Amberkite:" in original["transcript"]
        assert "nonexistent-term" not in original["body"]


@pytest.mark.parametrize("created", [None, "invalid", 42, [], {}, "2026-11-13T10:00:00"])
def test_recall_and_catch_up_with_invalid_created(tmp_path, created):
    client, inbox = make(tmp_path)
    valid = dumps.save_dump(inbox, "Valid capture", {"actions": ["Keep this"]}, source="web")
    invalid = dumps.save_dump(inbox, "Undated capture", {"actions": ["Skip this"]}, source="web")
    path = inbox / (invalid["id"] + ".md")
    body = path.read_text(encoding="utf-8")
    path.write_text(body.replace(json.dumps(invalid["created"]), json.dumps(created)), encoding="utf-8")

    listed = client.get("/dumps", headers=AUTH).json()["dumps"]
    assert [m["id"] for m in listed] == [valid["id"], invalid["id"]]
    assert listed[1]["created"] == ""
    recalled = client.get(f"/dumps/{invalid['id']}", headers=AUTH).json()
    assert recalled["created"] == "" and recalled["transcript"] == "Undated capture"
    result = dumps.catch_up(inbox, now=datetime.fromisoformat(valid["created"]))
    assert [m["id"] for m in result["dumps"]] == [valid["id"]]
    assert [a["text"] for a in result["actions"]] == ["Keep this"]
    assert result["skipped"] == 1


def test_console_recall_limit_sources_empty_and_failure():
    if not shutil.which("node"):
        pytest.skip("Node is required for the console JavaScript check")
    html = (Path(__file__).parents[1] / "mazhost/static/ui.html").read_text(encoding="utf-8")
    script = html.split("/* ---- source-backed recall:", 1)[1].split("/* ---- list ---- */", 1)[0]
    script = script[script.index("function sourceButton"):]
    check = r'''
const assert = require('node:assert/strict');
const el = (tag, text, cls) => ({tag, text, cls, children: [], append(...xs) { this.children.push(...xs); }, replaceChildren(...xs) { this.children = xs; }});
const nodes = Object.fromEntries(['recall', 'recallq', 'recallresults'].map(id => [id, el('div')]));
const $ = id => nodes[id], arr = x => x, str = x => x || '', fail = e => e.message;
let opened, view, calls = 0;
const select = id => { opened = id; }, showView = v => { view = v; };
const records = Array.from({length: 5}, (_, i) => ({id: 'source-' + i, created: '2026-11-13T10:00:00Z', source: 'web', body: 'Exact Amberkite <script> text ' + i}));
const API = {list: async () => ({dumps: records}), get: async id => { calls++; return records.find(d => d.id === id); }};
'''
    assertions = r'''
(async () => {
  const button = {};
  const submit = () => nodes.recall.onsubmit({preventDefault() {}, submitter: button});
  nodes.recallq.value = 'AMBERKITE';
  await submit();
  assert.equal(calls, 3);
  assert.equal(button.disabled, false);
  assert.equal(nodes.recallresults.children.length, 3);
  const card = nodes.recallresults.children[0];
  assert.match(card.children[0].text, /2026-11-13T10:00:00Z.*Source ID: source-0/);
  assert.equal(card.children[1].text, records[0].body);
  card.children[2].onclick();
  assert.equal(opened, 'source-0'); assert.equal(view, 'inbox');
  nodes.recallq.value = 'nonexistent-term'; await submit();
  assert.match(nodes.recallresults.children[0].text, /No saved captures match/);
  API.get = async () => { throw {status: 404}; };
  await submit(); assert.match(nodes.recallresults.children[0].text, /No saved captures match/);
  API.get = async () => { throw new Error('offline'); };
  await submit(); assert.match(nodes.recallresults.children[0].text, /Recall unavailable: offline/);
  assert.equal(button.disabled, false);
})().catch(error => { console.error(error); process.exit(1); });
'''
    result = subprocess.run(["node", "-"], input=check + script + assertions, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_saved_catch_up_window_and_unfinished_actions(tmp_path):
    now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)

    def saved(label, offset, status="new", actions=None):
        meta = dumps.save_dump(tmp_path, label, {"summary": label, "actions": actions or [label]}, source="web")
        path = tmp_path / (meta["id"] + ".md")
        content = path.read_text(encoding="utf-8")
        content = content.replace(meta["created"], (now + offset).isoformat())
        path.write_text(content, encoding="utf-8")
        if status == "done":
            dumps.finish_dump(tmp_path, meta["id"], "tester")
        elif status == "claimed":
            dumps.claim_dump(tmp_path, meta["id"], "tester")
        return meta["id"]

    old = saved("old", -timedelta(days=1, seconds=1))
    boundary = saved("boundary", -timedelta(days=1))
    claimed = saved("claimed", -timedelta(hours=1), "claimed", ["Call the room", "Bring the folder"])
    done = saved("finished", -timedelta(minutes=10), "done")
    newest = saved("latest", timedelta(0), actions=["Send the agenda"])
    future = saved("future", timedelta(seconds=1))
    result = dumps.catch_up(tmp_path, now=now)
    assert result["window_start"] == (now - timedelta(days=1)).isoformat()
    assert result["window_end"] == now.isoformat()
    assert [d["id"] for d in result["dumps"]] == [newest, done, claimed, boundary]
    assert [a["text"] for a in result["actions"]] == ["Send the agenda", "Call the room", "Bring the folder"]
    assert [a["id"] for a in result["actions"]] == [newest, claimed, claimed]
    assert all(a["id"] not in (done, old, future) for a in result["actions"])
    dumps.finish_dump(tmp_path, newest, "tester")
    assert [a["id"] for a in dumps.catch_up(tmp_path, now=now)["actions"]] == [claimed, claimed, boundary]


def test_saved_catch_up_empty_bad_date_and_checked_actions(tmp_path):
    assert dumps.catch_up(tmp_path)["dumps"] == []
    assert dumps.catch_up(tmp_path)["actions"] == []
    meta = dumps.save_dump(tmp_path, "neutral", {"actions": ["Keep", "Completed"]}, source="web")
    path = tmp_path / (meta["id"] + ".md")
    body = path.read_text(encoding="utf-8").replace("- [ ] Completed", "- [x] Completed")
    path.write_text(body, encoding="utf-8")
    assert [a["text"] for a in dumps.catch_up(tmp_path)["actions"]] == ["Keep"]
    path.write_text(body.replace(meta["created"], "invalid"), encoding="utf-8")
    result = dumps.catch_up(tmp_path)
    assert result["dumps"] == [] and result["actions"] == [] and result["skipped"] == 1


def test_saved_catch_up_authenticated_route_and_done_refresh(tmp_path):
    client, inbox = make(tmp_path, Models(fail=True))
    assert client.get("/dumps/catch-up").status_code == 401
    dump_id = dumps.save_dump(inbox, "Bring folder", {"actions": ["Bring folder"]}, source="web")["id"]
    result = client.get("/dumps/catch-up", headers=AUTH)
    assert result.status_code == 200
    assert result.json()["actions"][0]["id"] == dump_id
    client.post(f"/dumps/{dump_id}/done", headers=AUTH, json={"agent": "web"})
    assert client.get("/dumps/catch-up", headers=AUTH).json()["actions"] == []
    assert client.get("/dumps/catch-up", headers=AUTH).json()["dumps"][0]["id"] == dump_id


def test_console_catch_up_rendering_empty_failure_and_script_syntax():
    if not shutil.which("node"):
        pytest.skip("Node is required for the console JavaScript check")
    html = (Path(__file__).parents[1] / "mazhost/static/ui.html").read_text(encoding="utf-8")
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    parsed = subprocess.run(["node", "--check", "-"], input=script, text=True, capture_output=True)
    assert parsed.returncode == 0, parsed.stderr
    render = script.split("/* ---- saved-capture catch-up ---- */", 1)[1].split('$("catchuprefresh").onclick', 1)[0]
    navigation = "function showView(v)" + script.split("function showView(v)", 1)[1].split("async function needAct", 1)[0]
    check = r'''
const assert = require('node:assert/strict');
const el = (tag, text, cls) => ({tag, text, cls, children: [], append(...xs) { this.children.push(...xs); }, replaceChildren(...xs) { this.children = xs; }});
const nodes = {catchupcontent: el('div'), catchuprefresh: {}};
const $ = id => nodes[id], arr = x => x, str = x => x || '', fail = e => e.message;
const sourceMeta = d => el('span', d.id), sourceButton = d => el('button', d.id);
const API = {};
for (const id of ['catchup', 'needs', 'device', 'tab-inbox', 'tab-catchup', 'tab-needs', 'tab-device', 'rec']) {
  nodes[id] = {hidden: true, style: {}, attributes: {}, setAttribute(k, v) { this.attributes[k] = v; }};
}
const N = {}, D = {}, document = {body: {classList: {toggle() {}}, style: {setProperty() {}}}};
const refreshNeeds = () => {}, refreshDevice = () => {}, liveLoop = () => {};
'''
    assertions = r'''
(async () => {
  const empty = {window_start: 'START', window_end: 'END', skipped: 1, actions: [], dumps: []};
  renderCatchup(empty);
  const text = nodes.catchupcontent.children.map(n => n.text || '').join(' ');
  assert.match(text, /Coverage: START to END/);
  assert.match(text, /1 saved captures could not be included/);
  assert.match(text, /No saved captures in this window/);
  assert.match(text, /No stored actions from unfinished captures/);
  renderCatchup({...empty, skipped: 0, actions: [{id: 'a', text: '<script>action'}], dumps: [{id: 'a', summary: '<script>summary', status: 'new'}]});
  const cards = nodes.catchupcontent.children.filter(n => n.tag === 'article');
  assert.equal(cards.length, 2);
  assert.equal(cards[0].children[0].text, '<script>action');
  assert.equal(cards[1].children[0].text, '<script>summary');
  assert.equal(cards[0].children.at(-1).text, 'a');
  assert.equal(cards[1].children.at(-1).text, 'a');
  API.catchup = async () => empty;
  nodes['tab-catchup'].onclick();
  await new Promise(setImmediate);
  assert.equal(C.on, true);
  assert.equal(nodes.catchup.hidden, false);
  assert.equal(nodes['tab-catchup'].attributes['aria-pressed'], 'true');
  nodes['tab-inbox'].onclick();
  assert.equal(C.on, false); assert.equal(nodes.catchup.hidden, true);
  API.catchup = async () => { throw new Error('offline'); };
  await refreshCatchup();
  assert.match(nodes.catchupcontent.children[0].text, /Catch-up unavailable: offline/);
  assert.equal(nodes.catchuprefresh.disabled, false);
})().catch(error => { console.error(error); process.exit(1); });
'''
    result = subprocess.run(["node", "-"], input=check + render + navigation + assertions, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
