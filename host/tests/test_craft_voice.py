import io
import threading
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mazhost.config import Settings
from mazhost.security import Security
from mazhost.craft_voice import build_craft_voice_router


def wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        w.writeframes(b'\x00\x00' * 1600)
    return out.getvalue()


def setup(transcribe=lambda path: 'open browser', clock=None):
    api = FastAPI()
    kwargs = {} if clock is None else {'clock': clock}
    api.include_router(build_craft_voice_router(Security(Settings(_env_file=None)), transcribe, **kwargs))
    return TestClient(api)


def arm(c):
    result = c.post('/craft/voice/arm').json()
    return {'X-Craft-Capture': result['capture_id']}


def test_single_use_and_no_intent_processing():
    calls = []
    c = setup(lambda path: calls.append(path.read_bytes()) or 'open browser')
    h = arm(c)
    assert c.get('/craft/voice', headers=h).json() == {'status': 'waiting'}
    assert c.post('/craft/voice/audio', headers=h, content=wav()).json() == {'status': 'ready'}
    assert c.post('/craft/voice/audio', headers=h, content=wav()).status_code == 409
    assert c.get('/craft/voice', headers=h).json() == {'status': 'ready', 'text': 'open browser'}
    assert c.get('/craft/voice', headers=h).status_code == 404
    assert len(calls) == 1


def test_wrong_cancel_expiry_and_validation():
    now = [0.0]
    c = setup(clock=lambda: now[0])
    h = arm(c)
    assert c.get('/craft/voice', headers={'X-Craft-Capture': 'wrong'}).status_code == 404
    assert c.post('/craft/voice/audio', headers=h, content=b'bad').status_code == 400
    assert c.get('/craft/voice', headers=h).status_code == 404
    h = arm(c)
    assert c.delete('/craft/voice', headers=h).status_code == 204
    assert c.get('/craft/voice', headers=h).status_code == 404
    h = arm(c)
    now[0] = 61
    assert c.post('/craft/voice/audio', headers=h, content=wav()).status_code == 404


def test_cancel_while_transcribing_cannot_resurrect():
    started, release = threading.Event(), threading.Event()
    def slow(path):
        started.set()
        assert release.wait(5)
        return 'text'
    c = setup(slow)
    h = arm(c)
    result = []
    t = threading.Thread(target=lambda: result.append(c.post('/craft/voice/audio', headers=h, content=wav())))
    t.start()
    assert started.wait(5)
    assert c.delete('/craft/voice', headers=h).status_code == 204
    release.set()
    t.join(5)
    assert result[0].status_code == 404
    assert c.get('/craft/voice', headers=h).status_code == 404


def test_integrated_core_requires_auth_and_uses_local_stt(tmp_path):
    from test_app import FakePC, client
    pc = FakePC()
    c = client(pc=pc, work_dir=tmp_path)
    assert c.post('/craft/voice/arm').status_code == 401
    auth = {'Authorization': 'Bearer test-token-that-is-not-default'}
    capture = c.post('/craft/voice/arm', headers=auth).json()['capture_id']
    headers = {**auth, 'X-Craft-Capture': capture}
    assert c.post('/craft/voice/audio', headers=headers, content=wav()).status_code == 200
    assert c.get('/craft/voice', headers=headers).json() == {
        'status': 'ready', 'text': 'what should I focus on'}
    assert pc.actions == []


def test_claim_is_single_use_and_cannot_claim_cancelled_or_expired():
    now = [0.0]
    c = setup(clock=lambda: now[0])
    assert c.post('/craft/voice/claim').status_code == 404
    h = arm(c)
    now[0] = 2
    assert c.post('/craft/voice/claim').json() == {
        'capture_id': h['X-Craft-Capture'], 'expires_in': 58}
    assert c.post('/craft/voice/claim').status_code == 404
    assert c.delete('/craft/voice', headers=h).status_code == 204
    assert c.post('/craft/voice/claim').status_code == 404
    h = arm(c)
    assert c.delete('/craft/voice', headers=h).status_code == 204
    assert c.post('/craft/voice/claim').status_code == 404
    arm(c)
    now[0] = 63
    assert c.post('/craft/voice/claim').status_code == 404


def test_claim_refuses_ambiguous_captures():
    c = setup()
    first, second = arm(c), arm(c)
    assert c.post('/craft/voice/claim').status_code == 409
    assert c.delete('/craft/voice', headers=first).status_code == 204
    assert c.post('/craft/voice/claim').json()['capture_id'] == second['X-Craft-Capture']


def test_core_claim_requires_auth(tmp_path):
    from test_app import client
    c = client(work_dir=tmp_path)
    assert c.post('/craft/voice/claim').status_code == 401


def test_silence_reports_error_but_result_remains_consumable():
    c = setup(lambda path: '')
    h = arm(c)
    response = c.post('/craft/voice/audio', headers=h, content=wav())
    assert response.status_code == 422
    assert response.json()['detail'] == 'No speech detected. Try again.'
    assert c.get('/craft/voice', headers=h).json() == {'status': 'ready', 'text': ''}
    assert c.get('/craft/voice', headers=h).status_code == 404
