from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mazhost.authority import AuthorityBroker
from mazhost.config import Settings
from mazhost.phone_control import build_phone_app

# The session cookie phone_control.py sets is scoped Path=/control (matching
# how control_routes.py mounts it in production), so tests must mount the
# phone app the same way or the cookie is never sent back.


def _mounted_app(tmp_path) -> tuple[FastAPI, Settings]:
    settings = Settings(
        _env_file=None,
        token="test-token-123456789",
        control_dir=str(tmp_path / "control"),
        debug_dir=str(tmp_path / "debug"),
        work_dir=str(tmp_path / "work"),
        project_roots=str(tmp_path),
    )
    broker = AuthorityBroker(settings)
    root = FastAPI()
    root.mount("/control", build_phone_app(settings, broker))
    return root, settings


def make_client(tmp_path, *, user_agent: str = "test-phone") -> TestClient:
    root, settings = _mounted_app(tmp_path)
    client = TestClient(
        root,
        base_url="http://testserver/control/",
        headers={"user-agent": user_agent},
    )
    client.post("session/login", data={"token": settings.token})
    client.headers.update({"X-MAZ-Control": "1"})
    return client


def test_summary_requires_authenticated_session(tmp_path):
    root, _settings = _mounted_app(tmp_path)
    unauth = TestClient(root, base_url="http://testserver/control/")
    unauth.headers.update({"X-MAZ-Control": "1"})
    resp = unauth.get("work/summary")
    assert resp.status_code == 401


def test_summary_seeds_job_hunt_and_maz_works(tmp_path):
    client = make_client(tmp_path)
    resp = client.get("work/summary")
    assert resp.status_code == 200
    ids = {t["track_id"] for t in resp.json()["tracks"]}
    assert {"job_hunt", "maz_works"}.issubset(ids)


def test_quick_log_application_is_one_call_and_confirms(tmp_path):
    """Literal tap-count acceptance test (spec Section 2): from an already-open
    WORK tab, POSTing the quick-log-application event once records it and the
    server confirms — the one-tap common case."""
    client = make_client(tmp_path)
    resp = client.post(
        "work/tracks/job_hunt/events",
        json={"event_id": "evt_tap1", "event_type_id": "job_hunt.application"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["created"] is True
    summary = client.get("work/summary").json()
    job_hunt = next(t for t in summary["tracks"] if t["track_id"] == "job_hunt")
    assert job_hunt["today_total"] == 1


def test_duplicate_post_is_200_noop_never_409(tmp_path):
    client = make_client(tmp_path)
    body = {"event_id": "evt_retry", "event_type_id": "job_hunt.application"}
    first = client.post("work/tracks/job_hunt/events", json=body)
    second = client.post("work/tracks/job_hunt/events", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["created"] is False
    summary = client.get("work/summary").json()
    job_hunt = next(t for t in summary["tracks"] if t["track_id"] == "job_hunt")
    assert job_hunt["today_total"] == 1


def test_malformed_event_body_returns_422(tmp_path):
    client = make_client(tmp_path)
    resp = client.post("work/tracks/job_hunt/events", json={"event_type_id": ""})
    assert resp.status_code == 422


def test_oversized_event_note_returns_422(tmp_path):
    client = make_client(tmp_path)
    resp = client.post(
        "work/tracks/job_hunt/events",
        json={
            "event_type_id": "job_hunt.application",
            "note": "x" * 501,
        },
    )
    assert resp.status_code == 422


def test_non_finite_target_returns_422_not_a_serialization_error(tmp_path):
    client = make_client(tmp_path)
    resp = client.post(
        "work/tracks",
        content=(
            '{"name":"TEST","short_label":"TEST","mode":"count",'
            '"unit":"x","target":Infinity,"event_types":["DONE"]}'
        ),
    )
    assert resp.status_code == 422


def test_blank_custom_event_type_returns_422_not_500(tmp_path):
    client = make_client(tmp_path)
    resp = client.post(
        "work/tracks",
        json={
            "name": "TEST", "short_label": "TEST", "mode": "count", "unit": "x",
            "event_types": ["", "DONE"], "primary_event_type_index": 1,
        },
    )
    assert resp.status_code == 422


def test_oversized_custom_event_type_returns_422_instead_of_truncating(tmp_path):
    client = make_client(tmp_path)
    resp = client.post(
        "work/tracks",
        json={
            "name": "TEST", "short_label": "TEST", "mode": "count", "unit": "x",
            "event_types": ["x" * 61], "primary_event_type_index": 0,
        },
    )
    assert resp.status_code == 422


def test_undo_reverses_last_logged_event_via_api(tmp_path):
    client = make_client(tmp_path)
    logged = client.post(
        "work/tracks/job_hunt/events",
        json={"event_id": "evt_undo1", "event_type_id": "job_hunt.application"},
    ).json()["event"]
    undo = client.post(f"work/events/{logged['event_id']}/undo")
    assert undo.status_code == 200
    summary = client.get("work/summary").json()
    job_hunt = next(t for t in summary["tracks"] if t["track_id"] == "job_hunt")
    assert job_hunt["today_total"] == 0


def test_second_device_event_survives_first_device_undo(tmp_path):
    root, settings = _mounted_app(tmp_path)
    first = TestClient(
        root, base_url="http://testserver/control/", headers={"user-agent": "phone-a"}
    )
    second = TestClient(
        root, base_url="http://testserver/control/", headers={"user-agent": "phone-b"}
    )
    for client in (first, second):
        client.post("session/login", data={"token": settings.token})
        client.headers.update({"X-MAZ-Control": "1"})
    first.post(
        "work/tracks/job_hunt/events",
        json={"event_id": "evt_phone_a", "event_type_id": "job_hunt.application"},
    )
    second.post(
        "work/tracks/job_hunt/events",
        json={"event_id": "evt_phone_b", "event_type_id": "job_hunt.application"},
    )
    undo = first.post("work/events/evt_phone_a/undo")
    assert undo.status_code == 200
    job_hunt = next(
        t for t in second.get("work/summary").json()["tracks"]
        if t["track_id"] == "job_hunt"
    )
    assert job_hunt["today_total"] == 1


def test_maz_works_breakdown_never_implies_close_rate(tmp_path):
    client = make_client(tmp_path)
    client.post("work/tracks/maz_works/events", json={"event_type_id": "maz_works.outreach"})
    client.post("work/tracks/maz_works/events", json={"event_type_id": "maz_works.client_won"})
    summary = client.get("work/summary").json()
    maz_works = next(t for t in summary["tracks"] if t["track_id"] == "maz_works")
    # Raw counts only — no derived field like close_rate/score anywhere in the payload.
    assert "close_rate" not in maz_works and "score" not in maz_works
    outreach = next(e for e in maz_works["breakdown"] if e["event_type_id"] == "maz_works.outreach")
    won = next(e for e in maz_works["breakdown"] if e["event_type_id"] == "maz_works.client_won")
    assert outreach["total"] == 1
    assert won["total"] == 1
    assert maz_works["today_total"] == 2  # raw total activity, not only OUTREACH


def test_weekly_track_uses_current_local_week_for_target_progress(tmp_path, monkeypatch):
    from datetime import datetime
    from mazhost.work_service import WorkService
    from mazhost.work_store import WorkStore

    # A Wednesday gives an unambiguous Tuesday event in this week but not today.
    now = datetime(2026, 8, 26, 12, 0).timestamp()
    monkeypatch.setattr("mazhost.work_service.time.time", lambda: now)
    store = WorkStore(tmp_path / "weekly-work")
    store.bootstrap()
    track = store.create_track(
        track_id="custom_weekly", name="WEEKLY", short_label="WEEK",
        mode="count", unit="items", cadence="weekly", target=5,
        event_types=[("custom_weekly.done", "DONE", True)],
        primary_event_type_id="custom_weekly.done", pinned=True, sort_order=20,
    )
    for event_id, occurred_at in (("evt_tuesday", now - 86400), ("evt_old", now - 7 * 86400)):
        store.create_event(
            event_id=event_id, track_id=track["id"],
            event_type_id="custom_weekly.done", value=1,
            occurred_at=occurred_at, source="phone_manual", note=None,
            session_id="weekly-session",
        )

    weekly = next(
        item for item in WorkService(store).summary()["tracks"]
        if item["track_id"] == "custom_weekly"
    )
    assert weekly["today_total"] == 0
    assert weekly["current_total"] == 1
    assert weekly["current_window"] == "week"
    assert weekly["breakdown"][0]["total"] == 1


def test_create_custom_track_from_web_ui(tmp_path):
    client = make_client(tmp_path)
    resp = client.post(
        "work/tracks",
        json={
            "name": "PUSH-UPS", "short_label": "PUSHUPS", "mode": "count", "unit": "reps",
            "event_types": ["REP_SET"], "primary_event_type_index": 0, "pinned": True,
        },
    )
    assert resp.status_code == 200
    track = resp.json()["track"]
    assert track["mode"] == "count"
    log = client.post(f"work/tracks/{track['id']}/events", json={"event_type_id": track["event_types"][0]["id"]})
    assert log.status_code == 200


def test_event_type_rename_via_manage_tracks_preserves_history(tmp_path):
    client = make_client(tmp_path)
    client.post(
        "work/tracks/job_hunt/events",
        json={"event_id": "evt_before_rename", "event_type_id": "job_hunt.application"},
    )
    track = next(
        t for t in client.get("work/tracks").json()["tracks"]
        if t["id"] == "job_hunt"
    )
    application = next(e for e in track["event_types"] if e["id"] == "job_hunt.application")
    application["label"] = "SUBMITTED"
    resp = client.patch(
        "work/tracks/job_hunt",
        json={"event_types": [application]},
    )
    assert resp.status_code == 200
    summary = client.get("work/summary").json()
    job_hunt = next(t for t in summary["tracks"] if t["track_id"] == "job_hunt")
    assert job_hunt["today_total"] == 1
    renamed = next(
        e for e in job_hunt["breakdown"] if e["event_type_id"] == "job_hunt.application"
    )
    assert renamed["label"] == "SUBMITTED"


def test_manage_tracks_can_add_remove_restore_and_change_primary_event_type(tmp_path):
    client = make_client(tmp_path)
    created = client.post(
        "work/tracks",
        json={
            "name": "Build Loop", "short_label": "BUILD", "mode": "count",
            "unit": "steps", "event_types": ["DESIGN", "SHIP"], "pinned": True,
        },
    ).json()["track"]
    first, second = created["event_types"]
    assert client.post(
        f"work/tracks/{created['id']}/events",
        json={"event_id": "evt_before_remove", "event_type_id": first["id"]},
    ).status_code == 200

    first["active"] = False
    second["contributes_to_headline"] = False
    response = client.patch(
        f"work/tracks/{created['id']}",
        json={
            "primary_event_type_index": 2,
            "event_types": [
                first,
                second,
                {
                    "id": None, "label": "VERIFY", "sort_order": 2,
                    "active": True, "contributes_to_headline": True,
                },
            ],
        },
    )
    assert response.status_code == 200
    updated = response.json()["track"]
    removed = next(event for event in updated["event_types"] if event["id"] == first["id"])
    added = next(event for event in updated["event_types"] if event["label"] == "VERIFY")
    assert removed["active"] == 0, "remove must deactivate, not delete historical identity"
    assert updated["primary_event_type_id"] == added["id"]
    assert added["contributes_to_headline"] == 1
    assert client.post(
        f"work/tracks/{created['id']}/events",
        json={"event_id": "evt_inactive", "event_type_id": first["id"]},
    ).status_code == 400

    first["active"] = True
    restored = client.patch(
        f"work/tracks/{created['id']}", json={"event_types": [first]},
    )
    assert restored.status_code == 200
    summary = client.get("work/summary").json()
    track_summary = next(t for t in summary["tracks"] if t["track_id"] == created["id"])
    assert track_summary["today_total"] == 1, "restoring the type must reveal its preserved event"


def test_archive_removes_track_from_today_but_history_remains_queryable(tmp_path):
    client = make_client(tmp_path)
    client.post(
        "work/tracks/job_hunt/events",
        json={"event_id": "evt_before_archive", "event_type_id": "job_hunt.application"},
    )
    assert client.patch("work/tracks/job_hunt", json={"state": "archived"}).status_code == 200
    assert "job_hunt" not in {
        t["track_id"] for t in client.get("work/summary").json()["tracks"]
    }
    history = client.get("work/history?days=7").json()["history"]
    assert any(day["totals"].get("job_hunt") == 1 for day in history)


def test_summary_only_returns_pinned_active_tracks_in_stable_order(tmp_path):
    client = make_client(tmp_path)
    client.patch("work/tracks/job_hunt", json={"sort_order": 20})
    client.patch("work/tracks/maz_works", json={"sort_order": 10})
    custom = client.post(
        "work/tracks",
        json={
            "name": "UNPINNED", "short_label": "UNPIN", "mode": "count", "unit": "x",
            "event_types": ["DONE"], "pinned": False, "sort_order": 0,
        },
    ).json()["track"]
    ids = [t["track_id"] for t in client.get("work/summary").json()["tracks"]]
    assert ids == ["maz_works", "job_hunt"]
    assert custom["id"] not in ids


def test_invalid_window_and_unbounded_history_query_return_422(tmp_path):
    client = make_client(tmp_path)
    assert client.get("work/summary?window=forever").status_code == 422
    assert client.get("work/history?days=9999").status_code == 422


def test_cardputer_payload_caps_pinned_tracks_and_history_length(tmp_path):
    # The Cardputer-facing summary is a separate, bearer-token-authenticated
    # route on the main api app (mazhost.app.create_app) — the firmware never
    # holds the phone-session cookie used by /control/work/*.
    from mazhost.app import create_app

    client = make_client(tmp_path)
    for i in range(5):
        client.post(
            "work/tracks",
            json={
                "name": f"CUSTOM {i}", "short_label": f"C{i}", "mode": "count", "unit": "x",
                "event_types": ["DONE"], "pinned": True,
            },
        )
    root, settings = _mounted_app(tmp_path)
    api = create_app(settings)
    bearer_client = TestClient(api)
    resp = bearer_client.get("/work/cardputer", headers={"Authorization": f"Bearer {settings.token}"})
    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload["tracks"]) <= 4
    assert len(payload["seven_day"]) == 7
    assert payload["next_action"] == "Research one real prospect"
    assert all(track["primary_event_type_id"] for track in payload["tracks"])
    assert len(resp.content) <= 4096

    first = payload["tracks"][0]
    increment = bearer_client.post(
        "/work/cardputer/increment",
        headers={"Authorization": f"Bearer {settings.token}"},
        json={
            "event_id": "evt_cardputer_test",
            "track_id": first["track_id"],
            "event_type_id": first["primary_event_type_id"],
        },
    )
    assert increment.status_code == 200
    assert increment.json()["created"] is True


def test_client_pipeline_drives_today_from_stored_state_and_rejects_jobfinder(tmp_path):
    client = make_client(tmp_path)
    prospects = [
        {
            "item_id": f"client_{index}", "pipeline": "client",
            "title": f"Prospect {index}", "organisation": f"Business {index}",
            "stage": "REJECTED", "score": 30 + index,
            "source_url": f"https://example.com/{index}",
            "evidence": "Public booking flow reviewed.",
            "friction": "Manual enquiry handling.",
            "rationale": "Evidence-backed review.",
            "next_action": "",
        }
        for index in range(5)
    ]
    prospects[0].update({
        "stage": "QUALIFIED", "score": 86, "proof_status": "IN_PROGRESS",
        "solution": "A small enquiry triage mock-up.",
        "next_action": "Finish Prospect 0 enquiry triage mock-up",
    })
    prospects[1].update({
        "stage": "READY_TO_SEND", "score": 79, "proof_status": "READY",
        "outreach_status": "READY_TO_SEND", "outreach_draft": "Personalised draft",
        "next_action": "Review and send Prospect 1 outreach",
    })
    for prospect in prospects:
        response = client.post("work/pipeline", json=prospect)
        assert response.status_code == 200

    job = {
        "item_id": "job_real_1", "pipeline": "job", "title": "Automation Engineer",
        "organisation": "Example Employer", "stage": "READY_TO_APPLY", "score": 82,
        "source_url": "https://example.com/job", "evidence": "Live vacancy reviewed.",
        "rationale": "Strong Python and workflow fit.",
        "next_action": "Submit the prepared Automation Engineer application",
    }
    assert client.post("work/pipeline", json=job).status_code == 422

    today = client.get("work/today").json()
    assert today["stats"]["clients"] == {
        "prospects_researched": 5,
        "qualified": 2,
        "outreach_ready": 1,
        "outreach_sent": 0,
        "follow_ups_due": 0,
        "mini_solutions": 1,
    }
    assert "jobs" not in today["stats"]
    assert today["recommendations"][0]["item_id"] == "client_0"
    assert today["recommendations"][1]["item_id"] == "client_1"
    assert len(today["recommendations"]) == 2
    assert all(item["pipeline"] == "client" for item in today["recommendations"])
    assert "Finish Prospect 0 enquiry triage mock-up" in today["reply"]

    update = client.patch(
        "work/pipeline/client_0",
        json={"proof_status": "READY", "stage": "READY_TO_SEND"},
    )
    assert update.status_code == 200
    assert update.json()["item"]["stage"] == "READY_TO_SEND"


def test_history_bounded_to_seven_days(tmp_path):
    client = make_client(tmp_path)
    resp = client.get("work/history?days=7")
    assert resp.status_code == 200
    assert len(resp.json()["history"]) == 7


def test_app_today_card_route_answers_with_bearer_token(tmp_path):
    # Regression: the Piblo app Today card polls /work/today on the main api;
    # it was 404 because the route only existed under the phone-session /control mount.
    from mazhost.app import create_app

    _, settings = _mounted_app(tmp_path)
    api = create_app(settings)
    client = TestClient(api)
    headers = {"Authorization": f"Bearer {settings.token}"}
    resp = client.get("/work/today", headers=headers)
    assert resp.status_code == 200
    assert "items" in resp.json()
    assert client.get("/work/today").status_code == 401
