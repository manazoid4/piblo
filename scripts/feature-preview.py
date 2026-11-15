"""Prove memory or private PTT against a temporary Core on :18837, or --app to open an isolated desktop preview."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import io
import wave

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PORT = 18837
PTT_BRAIN_PORT = 18838


def private_ptt(token):
    """Real Kokoro -> Whisper -> local brain -> Kokoro; no microphone or speaker claim."""
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    seed = urllib.request.Request(f'http://127.0.0.1:{PORT}/speak', headers=headers,
                                  data=json.dumps({'text': 'Say hello in one short sentence.'}).encode())
    with urllib.request.urlopen(seed, timeout=90) as response:
        assert response.headers['X-TTS-Provider'] == 'kokoro'
        audio = response.read()
    sid = request('/session/start', token, {})['session_id']
    turn = urllib.request.Request(f'http://127.0.0.1:{PORT}/turn/raw', data=audio,
                                 headers={**headers, 'Content-Type': 'audio/wav', 'X-MAZ-Session': sid,
                                          'X-MAZ-Route': 'local', 'X-MAZ-Speak': '1'})
    with urllib.request.urlopen(turn, timeout=120) as response:
        result = json.load(response)
    assert result['provider'].startswith('local') and not result.get('degraded'), result
    assert 'hello' in result['text'].lower(), result
    for part in range(result['speak']['parts']):
        url = f"http://127.0.0.1:{PORT}/speak?id={result['speak']['id']}&part={part}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as response:
            assert response.headers['X-TTS-Provider'] == 'kokoro'
            with wave.open(io.BytesIO(response.read()), 'rb') as wav:
                assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
                assert wav.getnframes() > 0
    print('Private PTT passed:', json.dumps({k: result[k] for k in ('text', 'reply', 'provider', 'timing_ms')}))
    print('Real local STT/brain/Kokoro WAV verified; synthetic input, no device playback measured.')


def request(path, token=None, body=None, timeout=3):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{PORT}{path}', headers={**headers, 'Content-Type': 'application/json'}, data=json.dumps(body).encode() if body is not None else None), timeout=timeout) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--vault-dir')
    parser.add_argument('--wakeword-model-dir')
    parser.add_argument('--app', action='store_true', help='Open the built desktop app against the isolated Core; stop it by closing the window')
    parser.add_argument('--check-app', action='store_true', help='Like --app, but run the app self-check and exit')
    parser.add_argument('--check-memory', action='store_true', help='Synthetic Ask memory proof across a Core restart, using the configured local model')
    parser.add_argument('--check-private-ptt', action='store_true', help='Use installed model files with an isolated CPU llama-server on :18838; no live service calls')
    args = parser.parse_args()
    launch = args.app or args.check_app
    if not (args.check or args.check_private_ptt or launch):
        args.check_memory = True
    app = ROOT / 'app'
    if launch and not (app / 'dist/main/main.js').is_file():
        raise SystemExit('Build the app first: npm run build:main and npm run build:renderer')
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', PORT)) == 0:
            raise SystemExit(f'Port {PORT} is already in use; preview was not started.')
    with tempfile.TemporaryDirectory(prefix='piblo-preview-') as temp:
        tmp = Path(temp)
        token = secrets.token_urlsafe(32)
        vault = Path(args.vault_dir).expanduser().resolve() if args.vault_dir else tmp / 'demo'
        if args.vault_dir and not vault.is_dir():
            raise SystemExit('Vault folder does not exist.')
        if not args.vault_dir:
            vault.mkdir()
            (vault / 'Example note.txt').write_text('This is an isolated preview file.\n', encoding='utf-8')
        (tmp / '.env').write_text(f'MAZ_TOKEN={token}\nMAZ_PORT={PORT}\n', encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if not k.startswith(('MAZ_', 'PIBLO_', 'NOD_'))}
        installed = Path(os.environ.get('LOCALAPPDATA', '')) / 'Piblo/Core/.env'
        model_keys = ('MAZ_LOCAL_ENGINE', 'MAZ_LLAMACPP_URL', 'MAZ_LLAMACPP_MODEL', 'MAZ_OLLAMA_URL', 'MAZ_OLLAMA_MODEL', 'MAZ_OLLAMA_BACKUP_MODEL')
        if installed.is_file():
            env.update({k: v for k, v in dotenv_values(installed).items() if k in model_keys and v is not None})
        env.update({'PYTHONPATH': str(ROOT / 'host'), 'MAZ_TOKEN': token, 'MAZ_BIND': '127.0.0.1',
                    'MAZ_PORT': str(PORT), 'MAZ_DISCOVERY_PORT': '0', 'MAZ_AUTOUPDATE': 'false',
                    'MAZ_BRIDGE_ENABLED': 'false', 'MAZ_REMOTE_DETECT': 'false', 'MAZ_PRIVATE_MODE': 'true',
                    'MAZ_TTS_ENABLED': 'false', 'MAZ_BUDDY_DB': str(tmp / 'buddy.sqlite3'),
                    'MAZ_CORE_ENABLED': 'false', 'MAZ_NOW_PATH': str(tmp / 'NOW.md'), 'MAZ_LIVE_MEMORY_FILE': str(tmp / 'memory.md'),
                    'MAZ_VAULT_DIR': str(vault), 'MAZ_WAKEWORD': 'true' if args.wakeword_model_dir else 'false',
                    'MAZ_WAKEWORD_MODEL_DIR': args.wakeword_model_dir or ''})
        for key in ('DATA_DIR', 'WORK_DIR', 'CONTROL_DIR', 'DUMPS_DIR', 'TEACH_DIR', 'SKILL_VAULT_DIR',
                    'SKILL_DRAFT_DIR', 'DEBUG_DIR', 'FLOW_DIR'):
            env['MAZ_' + key] = str(tmp / key.lower())
        brain = None
        brain_log = None
        if args.check_private_ptt:
            assets = installed.parent.parent / 'models'
            binary = assets / 'llama-server/llama-server.exe'
            model = assets / 'llm/Qwen3-1.7B-Q4_K_M.gguf'
            kokoro = assets / 'kokoro/kokoro-v1.0.fp16.onnx'
            voices = assets / 'kokoro/voices-v1.0.bin'
            for asset in (binary, model, kokoro, voices):
                if not asset.is_file():
                    raise RuntimeError(f'Installed local asset missing: {asset.name}; no downloads attempted.')
            with socket.socket() as sock:
                if sock.connect_ex(('127.0.0.1', PTT_BRAIN_PORT)) == 0:
                    raise RuntimeError('Preview brain port is occupied; no existing process touched.')
            env.update({'MAZ_LOCAL_ENGINE': 'llamacpp', 'MAZ_LLAMACPP_URL': f'http://127.0.0.1:{PTT_BRAIN_PORT}',
                        'MAZ_LLAMACPP_MODEL': 'preview-local', 'MAZ_LLAMACPP_BACKUP_URL': '', 'MAZ_LLAMACPP_BACKUP_MODEL': '',
                        'MAZ_OLLAMA_URL': 'http://127.0.0.1:1', 'MAZ_OLLAMA_BACKUP_MODEL': '',
                        'MAZ_TTS_ENABLED': 'true', 'MAZ_TTS_PROVIDER': 'kokoro',
                        'MAZ_KOKORO_MODEL_PATH': str(kokoro), 'MAZ_KOKORO_VOICES_PATH': str(voices),
                        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'ONNX_PROVIDER': 'CPUExecutionProvider'})
            brain_log = (tmp / 'brain.log').open('w', encoding='utf-8')
            brain = subprocess.Popen([str(binary), '-m', str(model), '--host', '127.0.0.1',
                                      '--port', str(PTT_BRAIN_PORT), '-ngl', '0', '-c', '2048', '-t', '4'],
                                     cwd=tmp, env=env, stdout=brain_log, stderr=subprocess.STDOUT)
        core = None
        try:
            if brain is not None:
                for _ in range(120):
                    if brain.poll() is not None:
                        raise RuntimeError('Isolated local brain exited during startup.')
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{PTT_BRAIN_PORT}/health', timeout=1):
                            break
                    except OSError:
                        time.sleep(.25)
                else:
                    raise RuntimeError('Isolated local brain did not become ready.')
            core = subprocess.Popen([sys.executable, '-m', 'mazhost'], cwd=tmp, env=env)
            for _ in range(120):
                if core.poll() is not None:
                    raise RuntimeError('Preview Core exited during startup.')
                try:
                    if request('/health').get('ok') and 'notes' in request('/memory/recall', token):
                        break
                except (OSError, urllib.error.URLError):
                    time.sleep(.25)
            else:
                raise RuntimeError('Preview Core did not become ready.')
            try:
                request('/memory/recall')
                raise RuntimeError('Preview unexpectedly allowed unauthenticated access.')
            except urllib.error.HTTPError as error:
                if error.code not in (401, 403):
                    raise
            if args.check_private_ptt:
                assert request('/health', token)['private_mode'] is True
                private_ptt(token)
            if args.check_memory:
                session = request('/session/start', token, {})['session_id']
                saved = request('/turn/text', token, {'session_id': session, 'text': 'Remember that my appointment is Friday at noon.', 'route': 'local'})
                assert saved['provider'] == 'memory-local', saved
                request('/buddy/alert', token, {'source': 'reminder', 'kind': 'reminder', 'title': 'Synthetic reminder', 'delay_seconds': 8})
                core.terminate()
                core.wait(timeout=10)
                core = subprocess.Popen([sys.executable, '-m', 'mazhost'], cwd=tmp, env=env)
                for _ in range(120):
                    if core.poll() is not None:
                        raise RuntimeError('Restarted preview Core exited.')
                    try:
                        if request('/health').get('ok'):
                            break
                    except (OSError, urllib.error.URLError):
                        time.sleep(.25)
                else:
                    raise RuntimeError('Restarted preview Core did not become ready.')
                notes = request('/memory/recall?q=appointment', token)['notes']
                assert any('Friday at noon' in note['text'] for note in notes)
                fresh = request('/session/start', token, {})['session_id']
                recalled = request('/turn/text', token, {'session_id': fresh, 'text': 'When is my appointment?', 'route': 'local'}, timeout=90)
                assert not recalled.get('degraded'), recalled
                assert 'friday' in recalled['reply'].lower() and ('noon' in recalled['reply'].lower() or '12' in recalled['reply']), recalled
                print('Synthetic Ask memory survived a Core restart and a fresh conversation; local-model reply:', recalled['reply'])
                request('/buddy/alert', token, {'source': 'Phone', 'title': 'Mum', 'body': 'Synthetic: call tonight'})
                digest = request('/turn/text', token, {'session_id': fresh, 'text': 'Catch me up', 'route': 'local'})
                assert digest['provider'] == 'catchup-local' and 'Mum' in digest['reply'] and digest['reply'].endswith('Everything else can wait.'), digest
                print('Synthetic spoken Catch me up reply:', digest['reply'])
                for _ in range(60):   # the reminder was pending when Core stopped; it must still fire
                    if any(a['title'] == 'Synthetic reminder' and a['fired'] for a in request('/buddy/alerts', token)['alerts']):
                        break
                    time.sleep(.5)
                else:
                    raise AssertionError('Pending reminder was lost across the Core restart.')
                print('Pending reminder survived the Core restart and fired.')
            print('Isolated Core health and token authentication passed.')
            if launch:
                env.update({'NOD_CORE_DIR': str(tmp), 'NOD_CORE_PORT': str(PORT)})
                command = ['node', str(app / 'scripts/feature-preview.mjs'), str(tmp / 'userdata')]
                if args.check_app:
                    command.append('--check')
                subprocess.run(command, cwd=app, env=env, check=True)
        finally:
            for process in (core, brain):
                if process is None:
                    continue
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if brain_log:
                brain_log.close()


if __name__ == '__main__':
    main()
