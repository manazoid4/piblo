"""Tier 0 intents for the everyday capabilities: no model, microseconds.

Each pattern maps a spoken or typed request to one registered capability.
Order matters: "where did I leave off" is a resume, not a lost device.
"""

from __future__ import annotations

import re

_LEAD = r"^(?:hey\s+)?(?:piblo[,!.]?\s+)?(?:please\s+|can you\s+|could you\s+)*"

RESUME = re.compile(
    _LEAD + r"(?:carry on|pick (?:it |things )?(?:back )?up|continue|resume)(?: (?:from )?where (?:i|we) left off)?"
    r"(?: (?:my|the) (?:work|task))?[.!?]*$"
    r"|" + _LEAD + r"(?:where was i|where did (?:i|we) leave off|what was i (?:doing|working on))[.!?]*$",
    re.I)
SORT_OUT = re.compile(
    _LEAD + r"(?:sort (?:this|it) out|fix (?:this|it)|help me fix (?:this|it)|what'?s wrong(?: here| with (?:this|my (?:pc|computer|laptop)))?"
    r"|why (?:isn'?t|won'?t|is) (?:this|it) (?:working|loading|responding)|(?:my )?(?:pc|computer|laptop) is (?:being )?(?:slow|weird|broken|playing up))[.!?]*$",
    re.I)
RING = re.compile(_LEAD + r"(?:ring|beep|buzz|make (?:my |the )?(?P<q2>.+?) (?:ring|beep))(?: (?:my|the) (?P<q>.+?))?[.!?]*$", re.I)
FIND = re.compile(
    _LEAD + r"(?:find|locate|where (?:is|are|'s)|where'?s|where did i (?:put|leave)|i can'?t find|i'?ve lost|i lost|have you seen)"
    r"\s+(?:my\s+|the\s+)?(?P<q>.+?)[.!?]*$", re.I)
CONFIRM = re.compile(_LEAD + r"(?:yes|yeah|yep|ok(?:ay)?|sure|go ahead|do it|go on|carry on|ring it|open it|fix it)"
                     r"(?: please)?[.!?]*$", re.I)
CANCEL = re.compile(_LEAD + r"(?:stop|cancel|never ?mind|no|leave it|forget it)[.!?]*$", re.I)


def match(text: str) -> dict | None:
    """{"capability", "args", "reason"} or None. CONFIRM/CANCEL are follow-ups."""
    t = " ".join(text.strip().split())
    if not t:
        return None
    if CANCEL.match(t):
        return {"capability": None, "followup": "cancel", "args": {}, "reason": "cancel"}
    if CONFIRM.match(t):
        return {"capability": None, "followup": "confirm", "args": {}, "reason": "confirm"}
    if RESUME.match(t):
        return {"capability": "resume.packet", "args": {}, "reason": "resume phrase"}
    if SORT_OUT.match(t):
        return {"capability": "troubleshoot.diagnose", "args": {}, "reason": "troubleshoot phrase"}
    m = RING.match(t)
    if m and (m.group("q") or m.group("q2")):
        return {"capability": "devices.ring", "args": {"query": (m.group("q") or m.group("q2")).strip()},
                "reason": "ring phrase"}
    m = FIND.match(t)
    if m:
        q = m.group("q").strip()
        # "find a recipe" / "find out" are not lost devices.
        if not re.match(r"^(?:out|a|an|some|me|how|what|why|when)\b", q, re.I):
            return {"capability": "devices.find", "args": {"query": q}, "reason": "find phrase"}
    return None
