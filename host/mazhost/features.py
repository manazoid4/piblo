"""Feature graduation: every feature starts in Dev mode and reaches the main app once Maz confirms it works.

Features and their test steps come from the feature-tests checklist (one `## Heading` per feature).
Confirmations live in `<data>/dev/features.json`.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .dev_centre import atomic_write, file_lock

TEMPLATE = Path(__file__).parent / 'data' / 'dev_checklists' / 'feature-tests.md'
SUMMARIES = {
    'ask-on-piblo': 'Agents ask before risky steps; you allow or deny on Piblo.',
    'private-mode': 'One switch keeps everything on your own computers.',
    'where-was-i-park-it': 'Park a thought, then pick up where you left off.',
    'memory': 'Tell Piblo something once; it remembers.',
    'rescue': 'Your emergency contacts and notes, even offline.',
    'agent-builder': 'Describe a helper; Piblo drafts it for you to approve.',
    'speaks-first': 'Piblo speaks up when something is due.',
    'catch-me-up': 'A short summary of what you missed.',
    'buddy-forms': 'Buddy changes as you focus.',
    'onboarding': 'A few questions so Piblo fits you.',
    'maker': 'Printer status and safe pause or cancel.',
}

# These references describe the implementation, not proof of a device test.
CATALOGUE = {
    'ask-on-piblo': ('secure', 'host/mazhost/buddy.py', 'Piblo'),
    'private-mode': ('secure', 'host/mazhost/private_mode.py', 'None'),
    'where-was-i-park-it': ('everyday', 'host/mazhost/where.py', 'Piblo'),
    'memory': ('everyday', 'host/mazhost/memory_notes.py', 'None'),
    'rescue': ('everyday', 'host/mazhost/rescue.py', 'Piblo'),
    'agent-builder': ('workflows', 'host/mazhost/agent_builder.py', 'An available agent'),
    'speaks-first': ('buddy', 'host/mazhost/speaks_first.py', 'Audio output'),
    'catch-me-up': ('everyday', 'host/mazhost/catchup.py', 'None'),
    'buddy-forms': ('buddy', 'host/mazhost/buddy_forms.py', 'Piblo'),
    'onboarding': ('everyday', 'host/mazhost/onboarding.py', 'Piblo'),
    'maker': ('maker', 'host/mazhost/maker.py', 'Supported printer connection'),
    'device-pairing': ('connect', 'host/mazhost/pairing.py', 'Piblo and a computer on the same network'),
    'piblo-dev': ('dev', 'app/src/pages/PibloDev.tsx', 'Piblo Core and the Windows app'),
    'workflow-templates': ('workflows', 'host/mazhost/data/workflow_templates/templates.json', 'Piblo Core and the Windows app'),
    'voice-conversation': ('everyday', 'src/apps/comm.cpp', 'Piblo, microphone and configured voice services'),
    'capture-thoughts': ('everyday', 'host/mazhost/dumps.py', 'Piblo and Core'),
    'focus-timer': ('everyday', 'host/mazhost/focus.py', 'Piblo or the Windows app'),
    'needs-you': ('everyday', 'host/mazhost/needs.py', 'Piblo Core'),
    'saved-library': ('everyday', 'host/mazhost/library.py', 'Piblo Core'),
    'live-view': ('connect', 'host/mazhost/device.py', 'Connected Piblo'),
    'send-to-piblo': ('connect', 'host/mazhost/beam.py', 'Connected Piblo'),
    'phone-approvals': ('secure', 'host/mazhost/phone_control.py', 'Paired phone and Core'),
    'agent-jobs': ('dev', 'host/mazhost/agent_runner.py', 'Installed agent and phone approval'),
    'screen-teaching': ('dev', 'host/mazhost/teach_capture.py', 'A computer with screen capture'),
    'device-updates': ('connect', 'host/mazhost/fw.py', 'Compatible Piblo and an approved update'),
    'buddy-alerts': ('buddy', 'host/mazhost/buddy.py', 'Connected Piblo'),
    'today': ('everyday', 'src/apps/today.cpp', 'Piblo and Core'),
    'quiet-mode': ('everyday', 'src/core/quiet.cpp', 'Piblo'),
    'pause-approvals': ('secure', 'host/mazhost/buddy.py', 'Connected Piblo'),
    'quick-actions': ('everyday', 'src/apps/home.cpp', 'Piblo'),
    'work-tracks': ('workflows', 'host/mazhost/work_store.py', 'Piblo Core'),
    'device-status': ('connect', 'host/mazhost/device.py', 'Connected Piblo'),
    'utilities': ('everyday', 'src/apps/utility.cpp', 'Piblo'),
    'charging-screen': ('everyday', 'src/apps/charging.cpp', 'Piblo and a charger'),
    'dictation': ('everyday', 'src/audio/dictate.cpp', 'Piblo and configured speech recognition'),
    'hey-piblo': ('everyday', 'host/mazhost/wakeword.py', 'Open Live session, PC detector and optional model; hardware accuracy and model rights unconfirmed'),
    'voice-shortcuts': ('workflows', 'host/mazhost/shortcuts.py', 'Configured target, physical approval; commands also require signed PC authority'),
    'fetch-from-home': ('connect', 'host/mazhost/file_vault.py', 'Explicit home folder, paired Core; remote use needs home Tailscale URL/token'),
}

EXTRA_FEATURES = [
    {'id': 'device-pairing', 'name': 'Connect Piblo', 'summary': 'Approve a connection between Piblo and this PC.',
     'steps': ['Start Connect to computer on Piblo.', 'Review the request on the PC.', 'Allow it and check the connection.']},
    {'id': 'piblo-dev', 'name': 'Piblo Dev', 'summary': 'Review development work, checks and agent usage.',
     'steps': ['Open Piblo Dev in the Windows app.', 'Review Today, Agents and Builds.']},
    {'id': 'workflow-templates', 'name': 'Workflow templates', 'summary': 'Prepare a task from a reusable workflow.',
     'steps': ['Open Templates in Piblo Dev.', 'Use a template and save a task draft.']},
    {'id': 'voice-conversation', 'name': 'Talk to Piblo', 'summary': 'Ask a question by voice and hear a reply.',
     'steps': ['Start a voice turn on Piblo.', 'Ask a question.', 'Check the answer and audio.']},
    {'id': 'capture-thoughts', 'name': 'Capture a thought', 'summary': 'Save a thought to the inbox.',
     'steps': ['Capture a thought on Piblo.', 'Find it in Windows Memory.', 'Choose Read and verify the full original text, including its last sentence.']},
    {'id': 'focus-timer', 'name': 'Focus timer', 'summary': 'Start and finish a timed focus session.',
     'steps': ['Start a focus session in Windows Actions.', 'After expiry, check Finish and Stop remain available.', 'Choose Finish and verify the timer returns to idle.', 'Start another session, choose Stop and verify it ends without marking the task done.']},
    {'id': 'needs-you', 'name': 'Needs you', 'summary': 'See the items waiting for your attention.',
     'steps': ['Create an item that needs attention.', 'Open Actions and find the item.']},
    {'id': 'saved-library', 'name': 'Saved library', 'summary': 'Search items saved to your library.',
     'steps': ['Save an item in the library.', 'Search for a distinctive word in it.']},
    {'id': 'live-view', 'name': 'Live View', 'summary': 'See the Piblo screen on your PC.',
     'steps': ['Connect Piblo.', 'Open Live View.', 'Check that the screen updates.']},
    {'id': 'send-to-piblo', 'name': 'Send to Piblo', 'summary': 'Send a note or link to Piblo.',
     'steps': ['Send a short note from the Windows app.', 'Find it on Piblo.']},
    {'id': 'phone-approvals', 'name': 'Phone approvals', 'summary': 'Review a computer action from a paired phone.',
     'steps': ['Pair a phone.', 'Request an action.', 'Review and decide on the phone.']},
    {'id': 'agent-jobs', 'name': 'Agent jobs', 'summary': 'Start approved agent work and see its progress.',
     'steps': ['Request approval for an agent task.', 'Approve it on the phone.', 'Start and inspect the job.']},
    {'id': 'screen-teaching', 'name': 'Teach a workflow', 'summary': 'Capture a workflow for later review.',
     'steps': ['Record a narrated PC workflow.', 'Open Show me once on the PC and create a draft.', 'Review against the recording and explicitly save the workflow; this does not replay it.']},
    {'id': 'device-updates', 'name': 'Piblo updates', 'summary': 'Check for and stage a device update.',
     'steps': ['Check for an update.', 'Review the offered version before installation.']},
    {'id': 'buddy-alerts', 'name': 'Buddy alerts', 'summary': 'See reminders and alerts on Piblo.',
     'steps': ['Create a reminder or alert.', 'Check that it appears on Piblo.']},
    {'id': 'today', 'name': 'Today', 'summary': 'See what is due and what needs attention.',
     'steps': ['Open Today on Piblo.', 'Check the displayed items.']},
    {'id': 'quiet-mode', 'name': 'Quiet mode', 'summary': 'Reduce interruptions while you focus.',
     'steps': ['Turn on quiet mode.', 'Check its status.', 'Turn quiet mode off.']},
    {'id': 'pause-approvals', 'name': 'Pause asks', 'summary': 'Pause new agent approval requests.',
     'steps': ['Pause asks.', 'Check that requests do not proceed.', 'Resume asks.']},
    {'id': 'quick-actions', 'name': 'Quick actions', 'summary': 'Open common actions from Piblo.',
     'steps': ['Open the Piblo home screen.', 'Choose a quick action.']},
    {'id': 'work-tracks', 'name': 'Work tracks', 'summary': 'Keep progress for repeatable work.',
     'steps': ['Open a work track.', 'Record progress.', 'Check the updated count.']},
    {'id': 'device-status', 'name': 'Piblo status', 'summary': 'Check connection, battery and software status.',
     'steps': ['Connect Piblo.', 'Open Home in the Windows app.', 'Check the device status.']},
    {'id': 'utilities', 'name': 'Piblo utilities', 'summary': 'Open built-in tools such as a calculator and QR generator.',
     'steps': ['Open More on Piblo.', 'Open a utility and check its result.']},
    {'id': 'charging-screen', 'name': 'Charging screen', 'summary': 'See charging status on Piblo.',
     'steps': ['Connect a charger.', 'Check the charging screen.']},
    {'id': 'dictation', 'name': 'Dictation', 'summary': 'Turn spoken words into text.',
     'steps': ['Open dictation.', 'Speak a short phrase.', 'Check the resulting text.']},
    {'id': 'hey-piblo', 'name': 'Hey Piblo (experimental)', 'summary': 'Arm a local wake-word gate in an open Live session.',
     'steps': ['Install and configure the optional PC detector model.', 'Open Live, say Hey Piblo, pause, then speak a command.', 'Verify unrelated speech is ignored and playback re-arms the gate.']},
    {'id': 'voice-shortcuts', 'name': 'Approved voice shortcuts', 'summary': 'Run one configured webhook or authorised PC command.',
     'steps': ['Configure a fixed named target on the PC.', 'Ask for that shortcut and verify it waits for device approval.', 'Deny and verify no execution; allow a fresh request and verify one execution and its result.']},
    {'id': 'fetch-from-home', 'name': 'Fetch from Home', 'summary': 'Fetch an original document from a selected local or Tailscale home folder.',
     'steps': ['Opt in one home folder; configure the travelling PC connection if needed.', 'Ask for a document by name.', 'Fetch it in Windows Memory and verify original bytes; verify files outside the chosen folder stay unavailable.']},
]

HARDWARE_EVIDENCE = {
    'live-view': {'who': 'Owner', 'when': '2026-10-11', 'note': 'Screen mirror observed on an earlier build; see docs/evidence/TEST-LEDGER-2026-10-11.md, test 3.'},
    'capture-thoughts': {'who': 'Owner', 'when': '2026-10-11', 'note': 'A note reached the inbox on an earlier build; persistence was not tested. See docs/evidence/TEST-LEDGER-2026-10-11.md, test 5.'},
}


def slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    note: str = Field(default='', max_length=500)
    who: str = Field(default='', max_length=100)


def build_features_router(data_dir: Path) -> APIRouter:
    router = APIRouter(prefix='/features', tags=['features'])
    base = Path(data_dir) / 'dev'
    store = base / 'features.json'

    def catalogue() -> list[dict]:
        source = base / 'checklists' / 'feature-tests.md'
        text = (source if source.exists() else TEMPLATE).read_text(encoding='utf-8')
        rows, current = [], None
        for line in text.splitlines():
            if line.startswith('## '):
                name = line[3:].strip()
                ident = slug(name)
                category, source, dependencies = CATALOGUE.get(ident, ('dev', '', 'Unknown'))
                current = {'id': ident, 'name': name, 'summary': SUMMARIES.get(ident, name), 'steps': [],
                           'category': category, 'source': source, 'dependencies': dependencies}
                rows.append(current)
            elif current and (match := re.match(r'^\s*- \[[ xX]\] (.+)$', line)):
                current['steps'].append(match.group(1).strip())
        for extra in EXTRA_FEATURES:
            if any(row['id'] == extra['id'] for row in rows):
                continue
            category, source, dependencies = CATALOGUE[extra['id']]
            rows.append({**extra, 'category': category, 'source': source, 'dependencies': dependencies})
        return rows

    def load() -> dict:
        # Missing file = nothing confirmed. A damaged file is set aside and refused, never silently emptied
        # (otherwise the next confirm would wipe every other confirmation).
        if not store.exists():
            return {}
        try:
            state = json.loads(store.read_text(encoding='utf-8'))
            if isinstance(state, dict):
                return state
        except ValueError:
            pass
        store.replace(store.with_name(store.name + '.corrupt'))
        raise HTTPException(500, 'features_store_corrupt')

    def view(feature: dict, state: dict) -> dict:
        record = state.get(feature['id'], {})
        confirmed_at = record.get('confirmed_at')
        simulated = feature['id'] in {'private-mode', 'where-was-i-park-it', 'memory', 'rescue', 'agent-builder', 'speaks-first', 'catch-me-up', 'buddy-forms', 'maker', 'device-pairing', 'piblo-dev', 'workflow-templates', 'voice-conversation', 'capture-thoughts', 'focus-timer', 'needs-you', 'saved-library', 'live-view', 'phone-approvals', 'agent-jobs', 'screen-teaching', 'device-updates', 'buddy-alerts'}
        historical = HARDWARE_EVIDENCE.get(feature['id'])
        status = 'hardware-tested' if historical else ('simulated' if simulated else ('implemented' if feature['source'] else 'proposed'))
        return {**feature, 'status': 'confirmed' if confirmed_at else status,
                'confirmed_at': confirmed_at, 'note': record.get('note', ''),
                'evidence': {'who': record.get('who') or None, 'when': confirmed_at, 'note': record.get('note', '')} if confirmed_at else (historical or {'who': None, 'when': None, 'note': ''})}

    def find(feature_id: str) -> dict:
        feature = next((row for row in catalogue() if row['id'] == feature_id), None)
        if feature is None:
            raise HTTPException(404, 'feature_not_found')
        return feature

    def write(feature_id: str, record: dict | None) -> dict:
        feature = find(feature_id)
        base.mkdir(parents=True, exist_ok=True)
        with file_lock(store):
            state = load()
            if record is None:
                state.pop(feature_id, None)
            else:
                state[feature_id] = record
            atomic_write(store, json.dumps(state, indent=2))
        return view(feature, state)

    @router.get('')
    def list_features(dev_mode: bool = False):
        with file_lock(store):
            state = load()
        return {'features': [view(row, state) for row in catalogue() if dev_mode or row['category'] not in ('dev', 'electronics')]}

    @router.post('/{feature_id}/confirm')
    def confirm(feature_id: str, body: ConfirmBody | None = None):
        body = body or ConfirmBody()
        return write(feature_id, {'confirmed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'note': body.note.strip(), 'who': body.who.strip()})

    @router.post('/{feature_id}/unconfirm')
    def unconfirm(feature_id: str):
        return write(feature_id, None)

    return router
