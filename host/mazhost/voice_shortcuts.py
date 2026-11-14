"""Owner-configured phrases for existing low-risk PC controls."""
from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from .commands import parse_command

Action = Literal['desktop', 'play_pause', 'mute', 'volume_down', 'volume_up', 'previous_track', 'next_track']


def normalise(text: str) -> str:
    return ' '.join(text.lower().strip().rstrip('.!?').split())


class Shortcut(BaseModel):
    phrase: str = Field(min_length=1, max_length=60)
    actions: list[Action] = Field(min_length=1, max_length=10)

    @field_validator('phrase')
    @classmethod
    def validate_phrase(cls, phrase: str) -> str:
        phrase = normalise(phrase)
        if not re.fullmatch(r"[a-z0-9]+(?:[ '][a-z0-9]+)*", phrase) or parse_command(phrase):
            raise ValueError('Choose a distinct spoken phrase')
        return phrase


class ShortcutsIn(BaseModel):
    shortcuts: list[Shortcut] = Field(max_length=30)

    @model_validator(mode='after')
    def distinct(self):
        phrases = [s.phrase for s in self.shortcuts]
        if len(phrases) != len(set(phrases)):
            raise ValueError('Each phrase must be unique')
        return self


class VoiceShortcuts:
    def __init__(self, store, pc):
        self.store, self.pc = store, pc

    def load(self):
        if self.store is None:
            raise HTTPException(503, 'shortcut_storage_disabled')
        return ShortcutsIn.model_validate({'shortcuts': self.store.get_pref('voice_shortcuts', [])})

    def handle(self, text: str) -> str | None:
        if self.store is None:
            return None
        for shortcut in self.load().shortcuts:
            if normalise(text) == shortcut.phrase:
                try:
                    for action in shortcut.actions:
                        self.pc.perform(action)
                except RuntimeError:
                    return f"Sorry, I couldn't finish {shortcut.phrase}."
                return f"Done: {shortcut.phrase}."
        return None


def build_voice_shortcuts_router(shortcuts: VoiceShortcuts) -> APIRouter:
    router = APIRouter()

    @router.get('/voice-shortcuts')
    def get_shortcuts():
        return shortcuts.load().model_dump()

    @router.put('/voice-shortcuts')
    def put_shortcuts(body: ShortcutsIn):
        if shortcuts.store is None:
            raise HTTPException(503, 'shortcut_storage_disabled')
        shortcuts.store.set_pref('voice_shortcuts', body.model_dump()['shortcuts'])
        return body.model_dump()

    return router
