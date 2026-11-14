"""Deterministic voice intents over existing approved actions and home files."""
import re
import threading
import time

from .buddy import AlertIn


class FeatureCommands:
    def __init__(self, vault, shortcuts, buddy):
        self.vault, self.shortcuts, self.buddy = vault, shortcuts, buddy
        self._slots = threading.BoundedSemaphore(2)
        self._grants = {}

    def authorise(self, name, token):
        if name not in self.shortcuts.actions or not isinstance(self.shortcuts.actions[name], dict):
            raise ValueError('unknown_command_shortcut')
        self.shortcuts.executor.broker.verify_grant(token, required={'project_full'})
        self._grants[name] = token  # memory only; executor rechecks expiry/revocation at execution

    def handle(self, text):
        phrase = ' '.join(text.strip().rstrip('.!?').casefold().split())
        phrase = re.sub(r'^hey (?:piblo|peeblo)[, ]+', '', phrase)
        name = phrase.removeprefix('run ').removeprefix('shortcut ')
        if name in self.shortcuts.actions:
            if isinstance(self.shortcuts.actions[name], dict) and name not in self._grants:
                return 'Authorise this configured PC shortcut first, then ask me again.'
            if not self._slots.acquire(blocking=False):
                return 'Two shortcuts are already waiting. Please finish one first.'
            try:
                request = self.shortcuts.request(name)
                threading.Thread(target=self._run, args=(request['id'], name, self._grants.get(name, '')),
                                 daemon=True).start()
            except Exception:
                self._slots.release()
                raise
            return f'Please approve {name} on Piblo. I will report the result there.'
        match = re.fullmatch(r'(?:fetch|find|show|pull up) (?:my )?(.+?)(?: from home)?', phrase)
        if match:
            if not self.vault.enabled:
                return 'Choose your home folder on the PC before fetching files.'
            try:
                files = self.vault.search(match.group(1))['files']
            except (OSError, ValueError, RuntimeError):
                return 'Your home files are not answering right now. Check the home PC and Tailscale.'
            if not files:
                return 'I could not find that file in your selected home folder.'
            names = ', '.join(item['name'] for item in files[:3])
            return f'I found {names}. Open Fetch from Home in Memory to choose and fetch the original.'
        return None

    def _run(self, request_id, name, grant_token=''):
        try:
            deadline = time.monotonic() + 65
            result = {'status': 'pending'}
            while time.monotonic() < deadline:
                result = self.shortcuts.poll(request_id, grant_token=grant_token)
                if result['status'] != 'pending':
                    break
                time.sleep(0.25)
            status = result['status']
            if status == 'done':
                ok = result.get('ok', result.get('result', {}).get('ok', False))
                message = result.get('output', result.get('result', {}).get('stdout', ''))
                title = f'{name}: {"done" if ok else "returned an error"}'
            elif status == 'grant_required':
                title, message = name, 'Authorise PC command access before running this configured script.'
            else:
                title, message = name, f'Shortcut {status}; not automatically retried.'
            self.buddy.post_alert(AlertIn(source='shortcuts', kind='task_done',
                                          title=title[:60], body=str(message)[:200]))
        finally:
            self._slots.release()
