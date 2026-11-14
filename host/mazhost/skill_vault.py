from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import RouteError
from .llm import Models, Route


class SkillError(RuntimeError):
    pass


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (text or "maz-skill")[:64]


def _digest(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class SkillVault:
    def __init__(self, settings: Settings, models: Models) -> None:
        self.settings = settings
        self.models = models
        self.vault = Path(settings.skill_vault_dir).expanduser()
        self.drafts = Path(settings.skill_draft_dir).expanduser()
        self.teach_root = Path(settings.teach_dir).expanduser()
        self.vault.mkdir(parents=True, exist_ok=True)
        self.drafts.mkdir(parents=True, exist_ok=True)

    def _teach_source(self, session_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"teach_[A-Za-z0-9_-]{6,40}", session_id):
            raise SkillError("invalid_teach_session")
        root = self.teach_root / session_id
        manifest = root / "manifest.json"
        timeline = root / "timeline.jsonl"
        transcript = root / "transcript.json"
        if not manifest.exists():
            raise SkillError("teach_session_not_found")
        try:
            meta = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SkillError("teach_manifest_invalid") from error
        spoken = str(meta.get("transcript") or "")
        if transcript.exists():
            try:
                spoken = str(json.loads(transcript.read_text(encoding="utf-8")).get("text") or spoken)
            except (OSError, json.JSONDecodeError):
                pass
        events: list[dict[str, Any]] = []
        if timeline.exists():
            for line in timeline.read_text(encoding="utf-8", errors="replace").splitlines()[:500]:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("type") in {"mark", "session_start", "session_stop", "scene_frames", "transcript"}:
                    events.append(item)
        return {
            "root": root,
            "manifest": meta,
            "transcript": spoken,
            "events": events,
            "digests": {
                "manifest": _digest(manifest),
                "timeline": _digest(timeline),
                "transcript": _digest(transcript),
            },
        }

    @staticmethod
    def _fallback(session_id: str, transcript: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        text = " ".join(transcript.split()).strip()
        sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]
        marks = [str(e.get("note") or "").strip() for e in events if e.get("type") == "mark"]
        marks = [note for note in marks if note and note != "primary user mark"]
        steps = (sentences or marks)[:12] or ["Review the recording; no narrated or named steps were captured."]
        return {
            "name": f"Teach workflow {session_id[-6:]}",
            "description": "Repeat a workflow captured through Piblo Teach-by-Demonstration.",
            "when_to_use": ["When the same demonstrated workflow needs to be repeated."],
            "procedure": steps,
            "verification": ["Confirm the intended outcome matches the demonstrated successful state."],
            "safety": ["Re-check current project state and ask before expanding beyond demonstrated write scope."],
        }

    def draft_from_teach(self, session_id: str, route: Route = "auto") -> dict[str, Any]:
        source = self._teach_source(session_id)
        if source["manifest"].get("state") not in {"ready", "ready_partial"}:
            raise SkillError("teach_session_not_ready")
        transcript = source["transcript"]
        prompt = (
            "Turn this Piblo Teach-by-Demonstration evidence into one reusable procedural skill. "
            "Return JSON only with keys: name, description, when_to_use (array), procedure (array), "
            "verification (array), safety (array). Keep it concise and operational. Do not invent "
            "steps not supported by the demonstration; mark uncertain decisions as checks.\n\n"
            f"TRANSCRIPT:\n{transcript[:18000]}\n\n"
            f"KEY EVENTS:\n{json.dumps(source['events'][:100])}"
        )
        data: dict[str, Any]
        provider = "deterministic-fallback"
        try:
            reply, provider = self.models.chat(
                [
                    {"role": "system", "content": "You write portable open SKILL.md procedures. JSON only."},
                    {"role": "user", "content": prompt},
                ],
                route,
            )
            data = json.loads(reply)
            if not isinstance(data, dict):
                raise ValueError("not_object")
        except (RuntimeError, RouteError, json.JSONDecodeError, ValueError):
            data = self._fallback(session_id, transcript, source["events"])

        name = str(data.get("name") or "MAZ taught skill")[:100]
        slug = _slug(name)
        draft_id = "skill_" + secrets.token_urlsafe(10)
        root = self.drafts / draft_id
        root.mkdir(parents=True, exist_ok=False)
        skill_md = self._render_skill(name, data)
        (root / "SKILL.md").write_text(skill_md, encoding="utf-8")
        provenance = {
            "draft_id": draft_id,
            "name": name,
            "slug": slug,
            "state": "draft",
            "provider": provider,
            "created_at": time.time(),
            "teach_session": session_id,
            "source_digests": source["digests"],
            "teach_directory": str(source["root"]),
        }
        (root / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        (root / "sources.json").write_text(json.dumps({
            "teach_session": session_id,
            "events": source["events"],
        }, indent=2), encoding="utf-8")
        return {**provenance, "skill_md": skill_md}

    @staticmethod
    def _render_skill(name: str, data: dict[str, Any]) -> str:
        description = str(data.get("description") or "Reusable MAZ workflow.").replace("\n", " ")[:300]
        lines = [
            "---",
            f"name: {_slug(name)}",
            f"description: {description}",
            "version: 1.0.0",
            "metadata:",
            "  hermes:",
            "    category: maz-pocket",
            "    tags: [maz, taught-workflow]",
            "---",
            "",
            f"# {name}",
            "",
            "## When to use",
        ]
        for item in data.get("when_to_use") or []:
            lines.append(f"- {str(item).strip()}")
        lines += ["", "## Procedure"]
        for i, item in enumerate(data.get("procedure") or [], 1):
            lines.append(f"{i}. {str(item).strip()}")
        lines += ["", "## Verification"]
        for item in data.get("verification") or []:
            lines.append(f"- {str(item).strip()}")
        lines += ["", "## Safety / approval boundaries"]
        for item in data.get("safety") or []:
            lines.append(f"- {str(item).strip()}")
        lines += ["", "## Provenance", "Generated from an explicitly recorded Piblo Teach session; see `provenance.json` and `sources.json`.", ""]
        return "\n".join(lines)

    def draft(self, draft_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"skill_[A-Za-z0-9_-]{6,40}", draft_id):
            raise SkillError("invalid_draft_id")
        root = self.drafts / draft_id
        if not root.exists():
            raise SkillError("draft_not_found")
        provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
        return {**provenance, "skill_md": (root / "SKILL.md").read_text(encoding="utf-8")}

    def approve(self, draft_id: str, *, replace: bool = False) -> dict[str, Any]:
        draft = self.draft(draft_id)
        source = self.drafts / draft_id
        target = self.vault / str(draft["slug"])
        if target.exists() and not replace:
            raise SkillError("skill_already_exists")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        provenance_path = target / "provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["state"] = "approved"
        provenance["approved_at"] = time.time()
        provenance["vault_path"] = str(target)
        provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        return provenance

    def list_skills(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.vault.iterdir(), key=lambda p: p.name.lower()):
            if not path.is_dir() or not (path / "SKILL.md").exists():
                continue
            row = {"slug": path.name, "path": str(path)}
            provenance = path / "provenance.json"
            if provenance.exists():
                try:
                    row.update(json.loads(provenance.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    pass
            rows.append(row)
        return rows
