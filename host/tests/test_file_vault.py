import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mazhost.file_vault import FileVault, build_vault_router


def test_opt_in_and_original_document_roundtrip(tmp_path):
    assert FileVault().search('policy') == {'enabled': False, 'files': [], 'truncated': False}
    content = b'%PDF-1.4\nSynthetic travel insurance document'
    (tmp_path / 'Travel insurance policy.pdf').write_bytes(content)
    vault = FileVault(str(tmp_path))
    files = vault.search('travel policy')['files']
    assert len(files) == 1 and files[0]['path'] == 'Travel insurance policy.pdf'
    result = vault.fetch(files[0]['id'])
    assert base64.b64decode(result['content_base64']) == content
    assert result['mime'] == 'application/pdf'
    assert vault.search('not found')['files'] == []


def test_boundaries_and_opaque_identifiers(tmp_path):
    (tmp_path / '.hidden.txt').write_text('hidden')
    (tmp_path / 'credentials.json').write_text('never served')
    (tmp_path / 'run.exe').write_bytes(b'MZ')
    (tmp_path / 'too-big.pdf').write_bytes(b'x' * (10 * 1024 * 1024 + 1))
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git' / 'history.txt').write_text('hidden')
    vault = FileVault(str(tmp_path))
    assert vault.search()['files'] == []
    for ident in ['../outside', 'C:/Windows/win.ini', '0' * 24]:
        with pytest.raises(FileNotFoundError):
            vault.fetch(ident)


def test_symlink_outside_is_never_served(tmp_path):
    root = tmp_path / 'vault'
    root.mkdir()
    outside = tmp_path / 'private.txt'
    outside.write_text('outside')
    try:
        (root / 'link.txt').symlink_to(outside)
    except OSError:
        pytest.skip('symlink creation requires Windows privilege')
    assert FileVault(str(root)).search()['files'] == []


def test_http_search_fetch_and_download(tmp_path):
    (tmp_path / 'note.txt').write_text('home note')
    app = FastAPI()
    app.include_router(build_vault_router(FileVault(str(tmp_path))))
    client = TestClient(app)
    ident = client.get('/vault?q=note').json()['files'][0]['id']
    assert client.get('/vault/' + ident).json()['name'] == 'note.txt'
    assert base64.b64decode(client.get('/vault/' + ident).json()['content_base64']) == b'home note'
    assert client.get('/vault/not-an-id').status_code == 404


def test_file_swapped_after_lookup_cannot_escape_root(tmp_path, monkeypatch):
    root = tmp_path / 'vault'
    root.mkdir()
    path = root / 'note.txt'
    path.write_text('allowed')
    outside = tmp_path / 'private.txt'
    outside.write_text('outside')
    vault = FileVault(str(root))
    ident = vault.search()['files'][0]['id']
    original = vault.resolve

    def swap(file_id):
        result = original(file_id)
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            pytest.skip('symlink creation requires Windows privilege')
        return result

    monkeypatch.setattr(vault, 'resolve', swap)
    with pytest.raises((ValueError, OSError)):
        vault.fetch(ident)


def test_vault_routes_use_existing_core_auth_and_voice_lookup(tmp_path):
    from mazhost.app import create_app
    from mazhost.config import Settings
    import json

    folder = tmp_path / 'documents'
    folder.mkdir()
    (folder / 'travel insurance.pdf').write_bytes(b'%PDF synthetic')
    actions = tmp_path / 'shortcuts.json'
    actions.write_text(json.dumps({'check server': 'http://127.0.0.1:1/health'}))
    settings = Settings(_env_file=None, token='vault-test-token-not-default',
                        vault_dir=str(folder), shortcuts_file=str(actions),
                        data_dir=str(tmp_path / 'data'), work_dir=str(tmp_path / 'work'),
                        control_dir=str(tmp_path / 'control'), buddy_db='off')
    client = TestClient(create_app(settings))
    headers = {'Authorization': 'Bearer vault-test-token-not-default'}
    assert client.get('/vault').status_code == 401
    files = client.get('/vault', headers=headers).json()['files']
    assert client.get('/vault/' + files[0]['id']).status_code == 401
    session = client.post('/session/start', headers=headers).json()['session_id']
    answer = client.post('/turn/text', headers=headers,
                         json={'session_id': session, 'text': 'fetch my travel insurance', 'route': 'local'})
    assert answer.status_code == 200 and 'travel insurance.pdf' in answer.json()['reply']
    bad_session = client.post('/turn/text', headers=headers,
                              json={'session_id': 'not-real', 'text': 'check server', 'route': 'local'})
    assert bad_session.status_code == 404
    assert client.get('/buddy/pending', headers=headers).json()['pending'] == []
    invalid_grant = client.post('/shortcuts/authorise', headers=headers,
                                json={'name': 'check server', 'grant_token': 'invalid'})
    assert invalid_grant.status_code == 403, invalid_grant.text


def test_travelling_client_fetches_original_through_home_vault_api(tmp_path):
    import httpx
    from mazhost.file_vault import RemoteFileVault

    (tmp_path / 'insurance.pdf').write_bytes(b'%PDF original home document')
    home = FileVault(str(tmp_path))

    def transport(request):
        assert request.headers['Authorization'] == 'Bearer home-test-token'
        assert 'token' not in str(request.url)
        if request.url.path == '/vault':
            return httpx.Response(200, json=home.search(request.url.params.get('q', '')))
        return httpx.Response(200, json=home.fetch(request.url.path.rsplit('/', 1)[1]))

    remote = RemoteFileVault('http://100.64.0.2:8787', 'home-test-token', httpx.MockTransport(transport))
    result = remote.search('insurance')['files'][0]
    assert base64.b64decode(remote.fetch(result['id'])['content_base64']) == b'%PDF original home document'
    for url in ['http://example.com', 'https://user:password@home.ts.net', 'http://100.64.0.2/?token=x']:
        with pytest.raises(ValueError):
            RemoteFileVault(url, 'home-test-token')
