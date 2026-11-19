"""Share to Piblo: synthetic GitHub and Instagram input, no network or account access."""
from pathlib import Path

from mazhost.repo_capture import find_references, github_reference, mirror_to_unified_memory
from mazhost.share_in import Share, ShareIn
from mazhost import library


def memory_tree(tmp_path):
    memory = tmp_path / "unified-memory"
    (memory / "projects/piblo").mkdir(parents=True)
    (memory / "projects/piblo/STATUS.md").write_text("# Piblo", encoding="utf-8")
    (memory / "library/piblo-repos").mkdir(parents=True)
    (memory / "library/piblo-repos/README.md").write_text("# Repo Inbox", encoding="utf-8")
    return memory


def test_github_reference_canonicalises_and_rejects_host_spoof():
    item = github_reference("https://github.com/Storytold/Craft-Anything/tree/main?token=SENSITIVE")
    assert item["key"] == "storytold/craft-anything"
    assert item["url"] == "https://github.com/Storytold/Craft-Anything"
    for url in (
        "http://github.com/a/b", "https://github.com.evil.example/a/b",
        "https://user:secret@github.com/a/b", "https://github.com/a",
        "file:///github.com/a/b",
    ):
        assert github_reference(url) is None


def test_reference_discovery_from_reel_caption_never_fetches():
    refs = find_references(
        "Check https://github.com/SomeOrg/PibloTools and "
        "https://github.com/someorg/piblotools/tree/main and https://github.com.evil.example/a/b"
    )
    assert len(refs) == 1
    assert refs[0]["key"] == "someorg/piblotools"


def test_local_memory_mirror_is_truthful_idempotent_and_preserves_notes(tmp_path):
    memory = memory_tree(tmp_path)
    ref = github_reference("https://github.com/storytold/craft-anything")
    one = mirror_to_unified_memory(str(memory), ref, "Use this for Flow.")
    assert one["state"] == "local_checkout" and not one["pushed"]
    saved = memory / one["path"]
    original = saved.read_text(encoding="utf-8")
    two = mirror_to_unified_memory(str(memory), ref, "Use this for Flow.")
    assert two["status"] == "already_saved"
    assert saved.read_text(encoding="utf-8") == original
    mirror_to_unified_memory(str(memory), ref, "Also useful for onboarding")
    assert "Use this for Flow." in saved.read_text(encoding="utf-8")
    assert "Also useful for onboarding" in saved.read_text(encoding="utf-8")
    assert "unknown" in original.lower()


def test_memory_disabled_or_wrong_checkout_never_claims_synced(tmp_path):
    ref = github_reference("https://github.com/a/b")
    assert mirror_to_unified_memory("", ref, "hello")["state"] == "core_only"
    assert mirror_to_unified_memory(str(tmp_path), ref, "hello")["state"] == "core_only"
    assert not (tmp_path / "library").exists()


def test_share_github_saves_core_library_and_optionally_local_memory(tmp_path):
    memory = memory_tree(tmp_path)
    core = tmp_path / "core-library"
    svc = ShareIn(core, unified_memory_root=str(memory))
    r = svc.save(Share(url="https://github.com/SomeOrg/Tool/tree/main?secret=abc",
                       text="Use for Piblo: https://github.com/SomeOrg/Tool/tree/main?secret=abc"))
    assert r["kind"] == "github" and r["unified_memory"]["state"] == "local_checkout"
    assert r["github_candidates"] == ["https://github.com/SomeOrg/Tool"]
    saved = (memory / r["unified_memory"]["path"]).read_text(encoding="utf-8")
    assert "secret=abc" not in saved
    assert library.recent(core)[0]["id"] == r["id"]


def test_instagram_reel_is_bookmarked_not_falsely_watched(tmp_path):
    svc = ShareIn(tmp_path / "library")
    r = svc.save(Share(url="https://www.instagram.com/reel/ABC123/?igsh=x",
                       text="Look at this Reel: https://www.instagram.com/reel/ABC123/?igsh=x"))
    assert r["kind"] == "instagram"
    assert r["evidence"] == "shared_text_only"
    assert r["unified_memory"]["state"] == "not_a_github_repo"
    assert r["github_candidates"] == []
    found = svc.resurface("Look at this Reel")
    assert found and found[0]["id"] == r["id"]


def test_instagram_caption_discovers_github_but_does_not_auto_build(tmp_path):
    svc = ShareIn(tmp_path / "library")
    r = svc.save(Share(url="https://www.instagram.com/reel/ABC124/",
                       captions="An interesting build: https://github.com/ExampleOrg/Tool"))
    assert r["evidence"] == "captions_provided"
    assert r["github_candidates"] == ["https://github.com/ExampleOrg/Tool"]
    assert r["unified_memory"]["state"] == "not_a_github_repo"


def test_feed_vault_recent_endpoint_shows_source_and_repos(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mazhost.config import Settings
    from mazhost.library_routes import build_library_router
    from mazhost.dump_routes import inbox_dir

    cfg = Settings(dumps_dir=str(tmp_path), _env_file=None)
    root = inbox_dir(cfg) / "library"
    library.save_item(root, url="https://www.instagram.com/reel/XYZ789/",
                      text="Try https://github.com/Example/UsefulTools",
                      tags=["feed-vault", "link-only"])
    library.save_item(root, url="https://example.com/not-ig", text="other content")
    api = FastAPI()
    api.include_router(build_library_router(cfg, model_router=None))
    resp = TestClient(api).get("/library/recent", params={"kind": "instagram", "limit": 20})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1 and items[0]["kind"] == "instagram"
    assert items[0]["github_candidates"] == ["https://github.com/Example/UsefulTools"]
    assert "path" not in items[0]
    assert items[0]["url"] == "https://www.instagram.com/reel/XYZ789/"
