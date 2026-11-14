from __future__ import annotations

import json

from mazhost.config import Settings
from mazhost.skill_vault import SkillVault


class FakeModels:
    def chat(self, messages, route):
        return json.dumps({
            "name": "Audit project before build",
            "description": "Inspect project state and validate before building.",
            "when_to_use": ["Before a consequential project build."],
            "procedure": ["Read project instructions.", "Check git status.", "Run the relevant tests."],
            "verification": ["Tests pass and the intended project state is clear."],
            "safety": ["Do not discard unrelated working-tree changes."],
        }), "fake"


def test_teach_skill_is_draft_until_explicit_approval(tmp_path):
    teach = tmp_path / "teach" / "teach_abcdefghi"
    teach.mkdir(parents=True)
    (teach / "manifest.json").write_text(json.dumps({
        "session_id": "teach_abcdefghi",
        "state": "ready",
        "transcript": "First read the project instructions. Then check git status. Finally run tests.",
    }), encoding="utf-8")
    (teach / "timeline.jsonl").write_text(json.dumps({"t": 10, "type": "mark", "note": "tests"}) + "\n", encoding="utf-8")
    (teach / "transcript.json").write_text(json.dumps({"text": "Read instructions, check git status, run tests."}), encoding="utf-8")

    cfg = Settings(
        _env_file=None,
        token="configured-token",
        teach_dir=str(tmp_path / "teach"),
        skill_vault_dir=str(tmp_path / "skills"),
        skill_draft_dir=str(tmp_path / "drafts"),
    )
    vault = SkillVault(cfg, FakeModels())
    draft = vault.draft_from_teach("teach_abcdefghi", "local")
    assert draft["state"] == "draft"
    assert vault.list_skills() == []
    assert "## Procedure" in draft["skill_md"]

    approved = vault.approve(draft["draft_id"])
    assert approved["state"] == "approved"
    skills = vault.list_skills()
    assert len(skills) == 1
    assert skills[0]["slug"] == "audit-project-before-build"


def test_offline_draft_uses_named_marks_and_rejects_active_session(tmp_path):
    import pytest
    from mazhost.skill_vault import SkillError

    class Offline:
        def chat(self, messages, route):
            assert route == 'local'
            raise RuntimeError('offline')

    root = tmp_path / 'teach' / 'teach_abcdefghi'
    root.mkdir(parents=True)
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps({'state': 'recording'}), encoding='utf-8')
    (root / 'timeline.jsonl').write_text(json.dumps({'type': 'mark', 'note': 'Open the project'}) + '\n', encoding='utf-8')
    cfg = Settings(_env_file=None, teach_dir=str(root.parent), skill_vault_dir=str(tmp_path / 'skills'), skill_draft_dir=str(tmp_path / 'drafts'))
    vault = SkillVault(cfg, Offline())
    with pytest.raises(SkillError, match='teach_session_not_ready'):
        vault.draft_from_teach(root.name, 'local')
    manifest.write_text(json.dumps({'state': 'ready'}), encoding='utf-8')
    draft = vault.draft_from_teach(root.name, 'local')
    assert '1. Open the project' in draft['skill_md']
    assert vault.list_skills() == []


def test_teach_skill_routes_are_installed_and_require_token(tmp_path):
    from fastapi.testclient import TestClient
    from mazhost.app import create_app

    root = tmp_path / 'teach' / 'teach_abcdefghi'
    root.mkdir(parents=True)
    (root / 'manifest.json').write_text(json.dumps({'session_id': root.name, 'state': 'ready', 'created_at': 1, 'started_at': 1, 'capture': {}, 'directory': str(root), 'transcript': 'Open the project.'}), encoding='utf-8')
    cfg = Settings(_env_file=None, token='test-token', data_dir=str(tmp_path / 'data'), buddy_db='off', teach_dir=str(root.parent), skill_vault_dir=str(tmp_path / 'skills'), skill_draft_dir=str(tmp_path / 'drafts'), work_dir=str(tmp_path / 'work'))
    client = TestClient(create_app(cfg, models=FakeModels()))
    assert client.get('/skills').status_code == 401
    headers = {'Authorization': 'Bearer test-token'}
    assert any(s['session_id'] == root.name for s in client.get('/teach', headers=headers).json()['sessions'])
    response = client.post('/skills/draft/from-teach', headers=headers, json={'session_id': root.name, 'route': 'local'})
    assert response.status_code == 200, response.text
    draft = response.json()['draft']
    assert client.get('/skills', headers=headers).json()['skills'] == []
    assert client.post('/skills/draft/' + draft['draft_id'] + '/approve', headers=headers, json={}).status_code == 200
    assert len(client.get('/skills', headers=headers).json()['skills']) == 1
