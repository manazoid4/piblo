from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from . import private_mode
from .nowindow import no_window

TASK = re.compile(r'^(\s*- \[)([ xX])(\] )(.*?)(\r?\n)?$')
SUBS = ['esp32', 'M5Stack', '3Dprinting', 'arduino', 'functionalprint', 'ClaudeAI', 'LocalLLaMA', 'ChatGPTCoding']
_FILE_LOCKS = {}
_FILE_LOCKS_LOCK = threading.Lock()


def file_lock(path):
    # Resolving the file itself can briefly block os.replace on Windows.
    path = path.parent.resolve() / path.name
    with _FILE_LOCKS_LOCK:
        return _FILE_LOCKS.setdefault(path, threading.Lock())


def atomic_write(path, text):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=path.parent,
                                         prefix='.' + path.name + '-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def now():
    return datetime.now(timezone.utc).isoformat()


def item_id(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


class Tick(BaseModel):
    done: bool
    note: str | None = Field(default=None, max_length=2000)


class Target(BaseModel):
    platform: Literal['reddit', 'x', 'linkedin', 'instagram']
    subreddit: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_]{1,21}$')
    post_type: Literal['text', 'link'] = 'text'
    title: str | None = None
    body: str | None = None


class Draft(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(max_length=20000)
    media_path: str | None = None
    link: str | None = None
    targets: list[Target] = Field(default_factory=list, max_length=50)


class Feature(BaseModel):
    feature: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=10000)
    clip_path: str | None = None


class TaskDraft(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    task: str = Field(min_length=1, max_length=8000)


class MemorySettings(BaseModel):
    memory_repo_path: str = Field(default='', max_length=2048)


def run_cli(args, *, local=False, cwd=None):
    private_mode.guard_subprocess(args[0], local=local)
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=10, check=True, **no_window()).stdout


def usage():
    result = {'claude': {'available': False}, 'codex': {'available': False},
              'opencode': {'available': False}, 'hermes': {'available': False}}
    try:
        data = json.loads(run_cli(['cswap', 'list', '--json']))
        accounts = []
        for account in data['accounts']:
            source = account.get('usage') or account.get('lastGoodUsage') or {}
            row = {'number': account.get('number')}
            for key, dest in [('fiveHour', 'five_hour'), ('sevenDay', 'seven_day')]:
                value = source.get(key) or {}
                row[dest] = {'used_percent': value.get('pct'), 'resets_at': value.get('resetsAt')}
            accounts.append(row)
        result['claude'] = {'available': True, 'accounts': accounts}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, private_mode.PrivateModeError):
        pass
    try:
        sessions = list((Path.home() / '.codex' / 'sessions').glob('*/*/*/*.jsonl'))
        path = max(sessions, key=lambda p: p.stat().st_mtime)
        limits = None
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                    found = (record.get('payload') or {}).get('rate_limits') or record.get('rate_limits')
                    if found:
                        limits = found
                except (ValueError, AttributeError):
                    continue
        if limits:
            result['codex'] = {'available': True, **{key: {field: (limits.get(key) or {}).get(field)
                for field in ('used_percent', 'resets_at')} for key in ('primary', 'secondary')}}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return result


def build_dev_router(data_dir: Path, repo_dir: Path) -> APIRouter:
    router = APIRouter(prefix='/dev', tags=['dev'])
    base = Path(data_dir) / 'dev'
    checklists = base / 'checklists'
    content = base / 'content'
    cache = {'at': 0, 'value': None}

    def seed():
        with file_lock(checklists):
            checklists.mkdir(parents=True, exist_ok=True)
            if not any(checklists.iterdir()):
                for template in (Path(__file__).parent / 'data' / 'dev_checklists').glob('*.md'):
                    shutil.copyfile(template, checklists / template.name)

    def lists():
        seed()
        rows = []
        proof = base / 'feature-test-proof.json'
        for path in sorted(checklists.glob('*.md')):
            with file_lock(path):
                records = json.loads(proof.read_text(encoding='utf-8')) if path.stem == 'feature-tests' and proof.exists() else {}
                items = []
                for line in path.read_text(encoding='utf-8').splitlines(keepends=True):
                    match = TASK.match(line)
                    if match:
                        text = match.group(4)
                        ident = item_id(text)
                        item = {'id': ident, 'text': text, 'done': match.group(2).lower() == 'x'}
                        if path.stem == 'feature-tests' and item['done']:
                            item.update(records.get(ident, {}))
                        items.append(item)
            rows.append({'id': path.stem, 'items': items})
        return rows

    def content_path(ident):
        if not re.fullmatch(r'[0-9a-f]{32}', ident):
            raise HTTPException(404, 'draft_not_found')
        return content / (ident + '.json')

    def read(ident):
        try:
            return json.loads(content_path(ident).read_text(encoding='utf-8'))
        except FileNotFoundError as error:
            raise HTTPException(404, 'draft_not_found') from error

    def save(row):
        content.mkdir(parents=True, exist_ok=True)
        path = content_path(row['id'])
        atomic_write(path, json.dumps(row, ensure_ascii=False, indent=2))
        return row

    def create(draft):
        row = {**draft.model_dump(), 'id': uuid.uuid4().hex, 'status': 'draft', 'created': now(),
               'approved_at': None, 'posted_at': None}
        with file_lock(content_path(row['id'])):
            return save(row)

    @router.get('/checklists')
    def get_checklists():
        return {'lists': lists()}

    @router.post('/checklists/{list_id}/items/{ident}')
    def tick(list_id: str, ident: str, body: Tick):
        seed()
        if not re.fullmatch(r'[\w-]+', list_id):
            raise HTTPException(404, 'list_not_found')
        path = checklists / (list_id + '.md')
        with file_lock(path):
            if not path.is_file():
                raise HTTPException(404, 'list_not_found')
            with path.open(encoding='utf-8', newline='') as stream:
                lines = stream.readlines()
            matches = [i for i, line in enumerate(lines) if (m := TASK.match(line)) and item_id(m.group(4)) == ident]
            if not matches:
                raise HTTPException(404, 'item_not_found')
            if len(matches) != 1:
                raise HTTPException(409, 'duplicate_item_text')
            index = matches[0]
            match = TASK.match(lines[index])
            lines[index] = match.group(1) + ('x' if body.done else ' ') + match.group(3) + match.group(4) + (match.group(5) or '')
            atomic_write(path, ''.join(lines))
            if list_id == 'feature-tests':
                proof = base / 'feature-test-proof.json'
                records = json.loads(proof.read_text(encoding='utf-8')) if proof.exists() else {}
                if body.done:
                    records[ident] = {'when': now(), 'note': body.note}
                else:
                    records.pop(ident, None)
                atomic_write(proof, json.dumps(records, ensure_ascii=False, indent=2))
        return {'id': ident, 'done': body.done}

    @router.get('/content')
    def get_content():
        drafts = []
        for path in sorted(content.glob('*.json')):
            with file_lock(path):
                if path.is_file():
                    drafts.append(json.loads(path.read_text(encoding='utf-8')))
        return {'drafts': drafts}

    @router.post('/content')
    def create_content(body: Draft):
        return create(body)

    @router.post('/content/from-feature')
    def from_feature(body: Feature):
        title = f'Building Piblo: {body.feature}'
        text = f'{body.summary}\n\nMore at https://piblo.co.uk'
        x = (body.summary[:250] + '…' if len(body.summary) > 251 else body.summary) + '\n\nhttps://piblo.co.uk'
        titles = [title, f'Piblo build update: {body.feature}', f'Piblo project update: {body.feature}',
                  f'Building Piblo: {body.feature}', f'Piblo build notes: {body.feature}',
                  f'Piblo development: {body.feature}', f'Piblo project: {body.feature}',
                  f'Piblo coding update: {body.feature}']
        intros = ['A Piblo build update:', 'A Piblo project update:', 'An update from the Piblo build:',
                  'Sharing progress on Piblo:', 'Piblo build notes:', 'A Piblo development update:',
                  'Sharing progress on Piblo:', 'A Piblo coding update:']
        targets = [Target(platform='reddit', subreddit=sub, title=sub_title, body=intro + '\n\n' + text)
                   for sub, sub_title, intro in zip(SUBS, titles, intros)]
        targets += [Target(platform='x', body=x), Target(platform='linkedin', body=text)]
        return create(Draft(title=title, body=text, media_path=body.clip_path, link='https://piblo.co.uk', targets=targets))

    @router.put('/content/{ident}')
    def update_content(ident: str, body: Draft):
        with file_lock(content_path(ident)):
            row = read(ident)
            row.update(body.model_dump())
            row.update(status='draft', approved_at=None, posted_at=None)
            return save(row)

    def shares(row):
        if row['status'] not in ('approved', 'posted'):
            raise HTTPException(409, 'approval_required')
        result = []
        for target in row['targets']:
            platform = target['platform']
            text = target.get('body') or row['body']
            if platform == 'reddit':
                sub = target.get('subreddit')
                if not sub:
                    continue
                query = {'title': target.get('title') or row['title']}
                if target.get('post_type') == 'link':
                    if not row.get('link'):
                        raise HTTPException(422, 'link_required')
                    query['url'] = row['link']
                else:
                    query['text'] = text
                url = f'https://www.reddit.com/r/{sub}/submit?' + urlencode(query)
            elif platform == 'x':
                url = 'https://twitter.com/intent/tweet?' + urlencode({'text': text})
            elif platform == 'linkedin':
                url = 'https://www.linkedin.com/sharing/share-offsite/?' + urlencode({'url': row.get('link') or 'https://piblo.co.uk'})
            else:
                result.append({'platform': platform, 'caption': text, 'media_path': row.get('media_path')})
                continue
            result.append({'platform': platform, 'subreddit': target.get('subreddit'), 'url': url})
        return result

    @router.get('/content/{ident}/share')
    def share(ident: str):
        with file_lock(content_path(ident)):
            return {'shares': shares(read(ident))}

    @router.post('/content/{ident}/approve')
    def approve(ident: str):
        with file_lock(content_path(ident)):
            row = read(ident)
            if row['status'] == 'posted':
                raise HTTPException(409, 'already_posted')
            row.update(status='approved', approved_at=now())
            urls = shares(row)
            save(row)
            return {'draft': row, 'shares': urls}

    @router.post('/content/{ident}/posted')
    def posted(ident: str):
        with file_lock(content_path(ident)):
            row = read(ident)
            if row['status'] != 'approved':
                raise HTTPException(409, 'approval_required')
            row.update(status='posted', posted_at=now())
            return save(row)

    @router.delete('/content/{ident}')
    def delete(ident: str):
        with file_lock(content_path(ident)):
            read(ident)
            content_path(ident).unlink()
        return {'deleted': True}

    @router.get('/usage')
    def get_usage():
        return usage()

    @router.get('/workflow-templates')
    def workflow_templates():
        path = Path(__file__).parent / 'data' / 'workflow_templates' / 'templates.json'
        return {'templates': json.loads(path.read_text(encoding='utf-8'))}

    @router.get('/memory-settings')
    def memory_settings():
        path = base / 'memory-settings.json'
        with file_lock(path):
            return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'memory_repo_path': ''}

    @router.put('/memory-settings')
    def set_memory_settings(body: MemorySettings):
        value = body.memory_repo_path.strip()
        if value and not Path(value).expanduser().is_dir():
            raise HTTPException(422, 'memory_folder_not_found')
        base.mkdir(parents=True, exist_ok=True)
        path = base / 'memory-settings.json'
        with file_lock(path):
            atomic_write(path, json.dumps({'memory_repo_path': value}))
        return {'memory_repo_path': value}

    @router.get('/memory')
    def memory(q: str = ''):
        configured = memory_settings()['memory_repo_path']
        if not configured:
            return {'configured': False}
        root = Path(configured).expanduser().resolve()
        if not root.is_dir():
            raise HTTPException(503, 'memory_folder_unavailable')

        def read(path):
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(root) or not path.is_file() or path.stat().st_size > 1_000_000:
                    return ''
                return path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                return ''

        index = read(root / 'INDEX.md')
        handoff = root / 'handoffs' / 'LATEST.md'
        latest = read(handoff)
        results = []
        query = q.strip().casefold()[:200]
        if query:
            for folder, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if d != '.git' and not (Path(folder) / d).is_symlink()]
                for name in files:
                    if not name.lower().endswith('.md'):
                        continue
                    path = Path(folder) / name
                    try:
                        if not path.resolve().is_relative_to(root):
                            continue
                        with path.open(encoding='utf-8', errors='replace') as stream:
                            for line in stream:
                                if query in line.casefold():
                                    results.append({'path': path.relative_to(root).as_posix(), 'line': line.strip()[:500]})
                                    break
                    except OSError:
                        continue
                    if len(results) >= 20:
                        break
                if len(results) >= 20:
                    break
        try:
            updated = datetime.fromtimestamp(handoff.stat().st_mtime, timezone.utc).isoformat() if latest else None
        except OSError:
            updated = None
        return {'configured': True, 'headings': [line for line in index.splitlines() if re.match(r'^#{1,6}\s', line)][:100],
                'handoff': '\n'.join(latest.splitlines()[:20]), 'updated': updated, 'results': results}

    @router.post('/task-drafts')
    def create_task_draft(body: TaskDraft):
        folder = base / 'task-drafts'
        folder.mkdir(parents=True, exist_ok=True)
        row = {**body.model_dump(), 'id': uuid.uuid4().hex, 'status': 'draft', 'created': now()}
        atomic_write(folder / (row['id'] + '.json'), json.dumps(row, ensure_ascii=False, indent=2))
        return row

    @router.get('/task-drafts')
    def task_drafts():
        folder = base / 'task-drafts'
        return {'drafts': [json.loads(path.read_text(encoding='utf-8')) for path in sorted(folder.glob('*.json'))]}

    @router.get('/github')
    def github():
        if private_mode.enabled():
            return {'available': False}
        if cache['value'] is not None and time.monotonic() - cache['at'] < 60:
            return cache['value']
        try:
            prs = json.loads(run_cli(['gh', 'pr', 'list', '--json', 'number,title,headRefName,statusCheckRollup,url'], cwd=repo_dir))
            value = {'available': True, 'prs': prs}
        except (OSError, ValueError, subprocess.SubprocessError, private_mode.PrivateModeError):
            value = {'available': False}
        cache.update(at=time.monotonic(), value=value)
        return value

    @router.get('/overview')
    def overview():
        founder = next((row for row in lists() if row['id'] == 'founder-today'), {'items': []})
        gh = github()
        counts = sum(c.get('conclusion') in ('FAILURE', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED') or c.get('state') in ('FAILURE', 'ERROR')
                     for pr in gh.get('prs', []) for c in (pr.get('statusCheckRollup') or []))
        stats = usage()
        windows = [window for account in stats['claude'].get('accounts', []) for window in (account['five_hour'], account['seven_day'])]
        windows += [stats['codex'].get(key, {}) for key in ('primary', 'secondary')]
        return {'founder_unticked': sum(not item['done'] for item in founder['items']), 'failing_pr_checks': counts,
                'drafts_awaiting_approval': sum(row['status'] == 'draft' for row in get_content()['drafts']),
                'usage_warnings': sum(isinstance(w.get('used_percent'), (int, float)) and w['used_percent'] > 80 for w in windows)}

    return router
