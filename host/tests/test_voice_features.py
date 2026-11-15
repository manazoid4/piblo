import time
import hashlib
import hmac
from types import SimpleNamespace

import httpx

from mazhost.buddy import Buddy
from mazhost.file_vault import FileVault
from mazhost.shortcuts import Shortcuts
from mazhost.voice_features import FeatureCommands


def allow(buddy, request_id):
    buddy.device_keys['test-device'] = 'test-device-key'
    item = buddy._items[request_id]
    message = f"{request_id}:{item['digest']}:allow:{item['nonce']}".encode()
    proof = hmac.new(b'test-device-key', message, hashlib.sha256).hexdigest()
    buddy.decide(request_id, 'allow', proof=proof, device_id='test-device')


def wait_for_alert(buddy):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        alerts = buddy.alerts()['alerts']
        if alerts:
            return alerts[-1]
        time.sleep(0.02)
    raise AssertionError('no shortcut result')


def test_file_intent_uses_real_folder_and_ignores_general_questions(tmp_path):
    (tmp_path / 'travel insurance.pdf').write_bytes(b'%PDF')
    buddy = Buddy()
    service = FeatureCommands(FileVault(str(tmp_path)), Shortcuts({}, buddy), buddy)
    assert 'travel insurance.pdf' in service.handle('Pull up my travel insurance')
    assert service.handle('What is an insurance policy?') is None


def test_webhook_requires_approval_and_executes_once():
    calls = []
    buddy = Buddy()
    transport = httpx.MockTransport(lambda req: (calls.append(req), httpx.Response(200, text='healthy'))[1])
    shortcuts = Shortcuts({'check server health': 'http://localhost/health'}, buddy, transport=transport)
    service = FeatureCommands(FileVault(), shortcuts, buddy)
    reply = service.handle('Hey Piblo, check server health')
    assert 'approve' in reply and calls == []
    request_id = next(iter(shortcuts._requests))
    allow(buddy, request_id)
    alert = wait_for_alert(buddy)
    assert alert['body'] == 'healthy' and len(calls) == 1
    shortcuts.poll(request_id)
    assert len(calls) == 1


def test_voice_command_uses_existing_signed_grant_path(tmp_path):
    seen = []

    def verify(token, **kwargs):
        assert token == 'signed-grant'

    def run(**kwargs):
        seen.append(kwargs)
        return {'ok': True, 'stdout': 'script result'}

    executor = SimpleNamespace(broker=SimpleNamespace(verify_grant=verify), run=run)
    buddy = Buddy()
    shortcuts = Shortcuts({'check server': {'command': 'Get-Date', 'cwd': 'C:/work'}}, buddy, executor=executor)
    service = FeatureCommands(FileVault(), shortcuts, buddy)
    assert 'Authorise' in service.handle('check server') and seen == []
    service.authorise('check server', 'signed-grant')
    service.handle('check server')
    request_id = next(iter(shortcuts._requests))
    allow(buddy, request_id)
    assert wait_for_alert(buddy)['body'] == 'script result'
    assert seen[0]['grant_token'] == 'signed-grant'


def test_real_signed_grant_and_approved_powershell_shortcut(tmp_path):
    import os
    import pytest
    from mazhost.authority import AuthorityBroker
    from mazhost.config import Settings
    from mazhost.executor import ElevatedExecutor

    if os.name != 'nt':
        pytest.skip('Windows PowerShell integration')
    cfg = Settings(_env_file=None, token='test-shortcut-token-not-default',
                   control_dir=str(tmp_path / 'authority'))
    broker = AuthorityBroker(cfg)
    approved = broker.approve(broker.request(task='Test fixed print shortcut', agent='piblo',
                                             scope='project_full', project=str(tmp_path))['request_id'])
    buddy = Buddy()
    shortcuts = Shortcuts({'check server': {'command': 'Write-Output PIBLO_SHORTCUT_OK', 'cwd': str(tmp_path)}},
                          buddy, executor=ElevatedExecutor(cfg, broker))
    service = FeatureCommands(FileVault(), shortcuts, buddy)
    service.authorise('check server', approved['grant_token'])
    service.handle('check server')
    assert buddy.alerts()['alerts'] == []
    request_id = next(iter(shortcuts._requests))
    allow(buddy, request_id)
    alert = wait_for_alert(buddy)
    assert 'PIBLO_SHORTCUT_OK' in alert['body']
    assert shortcuts.poll(request_id)['result']['ok'] is True
