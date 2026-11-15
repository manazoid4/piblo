from __future__ import annotations

from pathlib import Path

import pytest

from mazhost.config import Settings
from mazhost.core import CoreError, MazCore
from mazhost.jobs import CoreJobs


def make_core(tmp_path: Path) -> tuple[MazCore, Path]:
    root = tmp_path / "Projects"
    project = root / "demo"
    project.mkdir(parents=True)
    (project / "package.json").write_text('{"name":"demo","scripts":{"build":"echo build"}}', encoding="utf-8")
    (project / "README.md").write_text("MAZ Core knows this factual demo project.\n", encoding="utf-8")
    settings = Settings(project_roots=str(root), core_enabled=True, _env_file=None)
    return MazCore(settings), project


def test_core_discovers_and_reads_only_inside_project(tmp_path: Path):
    core, project = make_core(tmp_path)
    projects = core.projects()
    assert [item["name"] for item in projects] == ["demo"]
    assert core.read_file("demo", "README.md")["content"].startswith("MAZ Core")
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    with pytest.raises(CoreError, match="path_outside_project"):
        core.read_file("demo", "../../secret.txt")


def test_secret_files_are_blocked(tmp_path: Path):
    core, project = make_core(tmp_path)
    for name in (".env", ".env.local", "credentials.json", "private.key"):
        (project / name).write_text("SECRET=1", encoding="utf-8")
        with pytest.raises(CoreError, match="secret_file_blocked"):
            core.read_file("demo", name)


def test_search_and_grounding_use_real_project_evidence(tmp_path: Path):
    core, _ = make_core(tmp_path)
    hits = core.search("factual demo", "demo")["hits"]
    assert hits and hits[0]["path"] == "README.md"
    context = core.context_for_prompt("what changed in demo?")
    assert "demo" in context
    assert "matched_project_evidence" in context
    assert "MAZ Core factual evidence" in context


def test_generic_grounding_and_health_do_not_summarize_every_repo(tmp_path: Path, monkeypatch):
    core, _ = make_core(tmp_path)
    calls = 0
    original = core._project_summary

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(core, "_project_summary", counted)
    status = core.status()
    assert status["project_count"] == 1
    assert calls == 0, "health/status must not fan out Git summaries"

    first = core.projects()
    second = core.projects()
    assert first == second
    assert calls == 1, "second project-list refresh should use the TTL cache"

    calls = 0
    context = core.context_for_prompt("give me a short focus suggestion")
    assert "known_projects" in context
    assert calls == 0, "generic chat should not run deep Git evidence in every repo"


def test_dispatch_has_no_generic_shell(tmp_path: Path):
    core, _ = make_core(tmp_path)
    with pytest.raises(CoreError, match="command_not_allowed"):
        core.dispatch({"command": "shell", "cmd": "whoami"})


def test_jobs_are_allowlisted_and_unknown_actions_rejected(tmp_path: Path):
    core, _ = make_core(tmp_path)
    jobs = CoreJobs(core)
    with pytest.raises(CoreError, match="action_not_allowed"):
        jobs.start("shell", "demo")


def test_invalidate_serves_stale_then_refreshes_in_background(tmp_path: Path, monkeypatch):
    import time
    core, _ = make_core(tmp_path)
    version = {"n": 1}
    monkeypatch.setattr(core, "_project_summary", lambda path: {"name": path.name, "behind": version["n"]})
    assert core.projects()[0]["behind"] == 1
    version["n"] = 2  # e.g. a git pull changed the branch counts
    core._invalidate_project_cache()
    deadline = time.monotonic() + 5
    while core.projects()[0]["behind"] != 2 and time.monotonic() < deadline:
        time.sleep(0.02)  # reads never block: they return the cached list while the rescan runs
    assert core.projects()[0]["behind"] == 2
