from fastapi.testclient import TestClient

from mazhost.app import create_app
from mazhost.config import Settings
from test_app import FakePC, FakeModels, FakeStt, FakeNudge


def test_saved_phrase_runs_ordered_actions_and_can_be_edited(tmp_path):
    pc = FakePC()
    cfg = Settings(token='shortcut-test-token', buddy_db=str(tmp_path / 'buddy.db'),
                   work_dir=str(tmp_path / 'work'), project_roots=str(tmp_path), _env_file=None)
    api = TestClient(create_app(cfg, pc=pc, models=FakeModels(), stt=FakeStt(), nudge=FakeNudge()))
    h = {'Authorization': 'Bearer shortcut-test-token'}
    saved = api.put('/voice-shortcuts', headers=h, json={'shortcuts': [
        {'phrase': 'start work', 'actions': ['desktop', 'mute']}]})
    assert saved.status_code == 200
    assert api.get('/voice-shortcuts', headers=h).json()['shortcuts'][0]['phrase'] == 'start work'
    sid = api.post('/session/start', headers=h).json()['session_id']
    turn = api.post('/turn/text', headers=h, json={'session_id': sid, 'text': 'Start work.'})
    assert turn.status_code == 200 and pc.actions == ['desktop', 'mute']
    api.put('/voice-shortcuts', headers=h, json={'shortcuts': [
        {'phrase': 'start work', 'actions': ['volume_down']}]})
    api.post('/turn/text', headers=h, json={'session_id': sid, 'text': 'start work'})
    assert pc.actions == ['desktop', 'mute', 'volume_down']
    assert api.put('/voice-shortcuts', headers=h, json={'shortcuts': [
        {'phrase': 'danger', 'actions': ['lock']}] }).status_code == 422
    assert api.get('/voice-shortcuts', headers=h).json()['shortcuts'][0]['actions'] == ['volume_down']


def test_persistence_validation_and_context_never_runs(tmp_path):
    pc = FakePC()
    cfg = Settings(token='shortcut-test-token', buddy_db=str(tmp_path / 'buddy.db'),
                   work_dir=str(tmp_path / 'work'), project_roots=str(tmp_path), _env_file=None)
    h = {'Authorization': 'Bearer shortcut-test-token'}
    def make_client():
        return TestClient(create_app(cfg, pc=pc, models=FakeModels(), stt=FakeStt(), nudge=FakeNudge()))
    first = make_client()
    assert first.put('/voice-shortcuts', headers=h, json={'shortcuts': [
        {'phrase': 'start work', 'actions': ['desktop', 'mute']}]}).status_code == 200
    api = make_client()
    assert api.get('/voice-shortcuts', headers=h).json()['shortcuts'][0]['actions'] == ['desktop', 'mute']
    duplicate = {'shortcuts': [{'phrase': 'start work', 'actions': ['mute']},
                               {'phrase': 'Start Work.', 'actions': ['desktop']}]}
    assert api.put('/voice-shortcuts', headers=h, json=duplicate).status_code == 422
    assert api.put('/voice-shortcuts', headers=h, json={'shortcuts': [
        {'phrase': 'mute', 'actions': ['desktop']}]}).status_code == 422
    sid = api.post('/session/start', headers=h).json()['session_id']
    api.post('/turn/text', headers=h, json={'session_id': sid, 'text': 'start work', 'context': 'selected text'})
    assert pc.actions == []
    api.post('/turn/text', headers=h, json={'session_id': sid, 'text': 'should I start work?'})
    assert pc.actions == []


def test_failed_step_stops_sequence(tmp_path):
    from mazhost.buddy_store import BuddyStore
    from mazhost.voice_shortcuts import ShortcutsIn, VoiceShortcuts

    class FailingPC(FakePC):
        def perform(self, action):
            if action == 'mute':
                raise RuntimeError('failed')
            return super().perform(action)

    pc = FailingPC()
    store = BuddyStore(tmp_path / 'buddy.db')
    store.set_pref('voice_shortcuts', ShortcutsIn.model_validate({'shortcuts': [
        {'phrase': 'start work', 'actions': ['desktop', 'mute', 'volume_down']}]}).model_dump()['shortcuts'])
    assert "couldn't finish" in VoiceShortcuts(store, pc).handle('start work')
    assert pc.actions == ['desktop']


def test_raw_audio_and_live_voice_dispatch_use_saved_phrase(tmp_path, monkeypatch):
    import asyncio
    import io
    import wave
    import mazhost.app as app_module

    captured = {}
    def capture_live(api, cfg, security, timings, command_handler):
        captured['handler'] = command_handler

    class ShortcutSTT(FakeStt):
        def transcribe(self, path):
            return 'start work'

    monkeypatch.setattr(app_module, 'install_live_voice', capture_live)
    pc = FakePC()
    cfg = Settings(token='shortcut-test-token', buddy_db=str(tmp_path / 'buddy.db'),
                   work_dir=str(tmp_path / 'work'), project_roots=str(tmp_path), _env_file=None)
    api = TestClient(create_app(cfg, pc=pc, models=FakeModels(), stt=ShortcutSTT(), nudge=FakeNudge()))
    h = {'Authorization': 'Bearer shortcut-test-token'}
    api.put('/voice-shortcuts', headers=h, json={'shortcuts': [
        {'phrase': 'start work', 'actions': ['desktop', 'mute']}]})
    sid = api.post('/session/start', headers=h).json()['session_id']
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\0\0' * 1000)
    response = api.post('/turn/raw', headers={**h, 'X-MAZ-Session': sid}, content=audio.getvalue())
    assert response.status_code == 200 and pc.actions == ['desktop', 'mute']
    assert asyncio.run(captured['handler']('Start work.')) == 'Done: start work.'
    assert pc.actions == ['desktop', 'mute', 'desktop', 'mute']
