"""Literal browser-driven tap-count acceptance test (spec Section 2):

"From an already-open WORK tab, tap the data-testid="quick-log-application"
button once -> event is recorded, UI confirms. That is the 1-tap common case."

Runs the real phone-control FastAPI app under uvicorn and drives it with a
real Chromium browser via Playwright — not a description, an actual script.
Skips cleanly (not a failure) if Playwright's Chromium binary isn't installed
in this environment, since `playwright install chromium` is a separate,
environment-specific setup step outside this test's control."""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI

from mazhost.authority import AuthorityBroker
from mazhost.config import Settings
from mazhost.pairing import PairingStore
from mazhost.phone_control import build_phone_app

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - environment-dependent
    sync_playwright = None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chromium_available() -> bool:
    if sync_playwright is None:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


_BROWSER_AVAILABLE = _chromium_available()


@pytest.mark.skipif(
    not _BROWSER_AVAILABLE and not os.environ.get("CI"),
    reason="Playwright Chromium not installed in this local environment",
)
def test_one_tap_quick_log_application_from_open_work_tab(tmp_path):
    assert _BROWSER_AVAILABLE, "CI must install Playwright Chromium; browser acceptance cannot be skipped"
    settings = Settings(
        _env_file=None,
        token="e2e-pairing-token-123456",
        control_dir=str(tmp_path / "control"),
        debug_dir=str(tmp_path / "debug"),
        work_dir=str(tmp_path / "work"),
        project_roots=str(tmp_path),
    )
    broker = AuthorityBroker(settings)
    codes = PairingStore()
    code, _ = codes.start()
    root = FastAPI()
    root.mount("/control", build_phone_app(settings, broker, browser_codes=codes))

    port = _free_port()
    config = uvicorn.Config(root, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn server did not start in time"

        base = f"http://127.0.0.1:{port}/control/"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: console_errors.append(str(exc)))
            try:
                page.goto(base)
                page.fill('input[name="code"]', "wrong")
                page.click("form button")
                page.wait_for_selector('#sign-in-error', state="visible")
                page.fill('input[name="code"]', code)
                page.click("form button")
                page.wait_for_url(base)

                # WORK tab is already the default/open tab per spec Section 5 —
                # no extra tap needed to reach it.
                try:
                    page.wait_for_selector('[data-testid="quick-log-application"]', state="visible", timeout=10000)
                except Exception:
                    raise AssertionError(f"console errors: {console_errors}; html: {page.content()[:3000]}")
                before = page.text_content("#workFreshness")

                # The one tap.
                page.click('[data-testid="quick-log-application"]')

                # UI confirms: freshness/today total updates within a few polls.
                page.wait_for_function(
                    "(prev) => document.getElementById('workFreshness').textContent !== prev",
                    arg=before,
                    timeout=8000,
                )
                page.wait_for_selector("#workCards .card")
                cards_text = page.text_content("#workCards")
                assert "1" in cards_text

                # Browser refresh must reconstruct from server truth, not an
                # optimistic client-only counter.
                page.reload()
                page.wait_for_selector('[data-testid="quick-log-application"]', state="visible")
                summary = page.evaluate(
                    "() => fetch('work/summary?window=today', {headers:{'X-MAZ-Control':'1'}, cache:'no-store'}).then(r => r.json())"
                )
                job_hunt = next(t for t in summary["tracks"] if t["track_id"] == "job_hunt")
                assert job_hunt["today_total"] == 1

                # A rapid physical double-tap produces only one additional
                # request because the button disables synchronously for 600ms.
                page.eval_on_selector(
                    '[data-testid="quick-log-application"]',
                    "button => { button.click(); button.click(); }",
                )
                page.wait_for_function(
                    "() => !document.querySelector('[data-testid=quick-log-application]').disabled",
                    timeout=8000,
                )
                summary = page.evaluate(
                    "() => fetch('work/summary?window=today', {headers:{'X-MAZ-Control':'1'}, cache:'no-store'}).then(r => r.json())"
                )
                job_hunt = next(t for t in summary["tracks"] if t["track_id"] == "job_hunt")
                assert job_hunt["today_total"] == 2

                # The response replaces the original button DOM node. A second
                # post-response tap during the same 600ms debounce window must
                # still be refused by the global guard, not receive a new UUID.
                with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and "/work/tracks/job_hunt/events" in response.url
                ):
                    page.click('[data-testid="quick-log-application"]')
                page.wait_for_function(
                    "() => document.querySelector('[data-testid=quick-log-application]').disabled",
                    timeout=8000,
                )
                page.evaluate(
                    "() => quickLog(document.querySelector('[data-testid=quick-log-application]'))"
                )
                page.wait_for_function(
                    "() => !document.querySelector('[data-testid=quick-log-application]').disabled",
                    timeout=8000,
                )
                summary = page.evaluate(
                    "() => fetch('work/summary?window=today', {headers:{'X-MAZ-Control':'1'}, cache:'no-store'}).then(r => r.json())"
                )
                job_hunt = next(t for t in summary["tracks"] if t["track_id"] == "job_hunt")
                assert job_hunt["today_total"] == 3

                # Raw event counts are visible (no close-rate/productivity
                # composite), and normal polling must not erase an unsaved edit.
                page.click('[data-track="maz_works"][data-et="maz_works.outreach"]')
                page.wait_for_function(
                    "() => !document.querySelector('[data-testid=quick-log-application]').disabled",
                    timeout=8000,
                )
                assert "OUTREACH 1" in page.text_content("#workCards")
                name_editor = '[data-track-card="job_hunt"] [data-field="name"]'
                page.fill(name_editor, "UNSAVED NAME")
                page.wait_for_timeout(4500)
                assert page.input_value(name_editor) == "UNSAVED NAME"

                for width in (360, 390, 430):
                    page.set_viewport_size({"width": width, "height": 800})
                    assert page.evaluate(
                        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"
                    )
                assert page.eval_on_selector(
                    '[data-testid="quick-log-application"]',
                    "button => button.getBoundingClientRect().height",
                ) >= 44
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
