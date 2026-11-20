from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

def get_ledger_dir(root: Path | None = None) -> Path:
    if root:
        return root
    if os.environ.get("PIBLO_LEDGER_DIR"):
        return Path(os.environ["PIBLO_LEDGER_DIR"])
    return Path.home() / "Documents" / "Piblo" / "ledger"

def append_ledger(kind: str, text: str, root: Path | None = None, now: datetime | None = None) -> Path:
    text = text.strip()
    if not text:
        raise ValueError("Empty text")
    if len(text) > 20000:
        raise ValueError("Text too long")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", kind):
        raise ValueError("Invalid kind")
    
    now = now or datetime.now()
    ledger_dir = get_ledger_dir(root)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = ledger_dir / f"{now.date().isoformat()}.md"
    is_new = not file_path.exists()
    
    # Sanitize text to prevent fake headings
    lines = text.splitlines()
    sanitized_text = lines[0] if lines else ""
    for line in lines[1:]:
        sanitized_text += "\n  " + line

    with open(file_path, "a", encoding="utf-8", newline="\n") as f:
        if is_new:
            f.write(f"# Piblo ledger {now.date().isoformat()}\n\n")
        f.write(f"## {now.strftime('%H:%M')} {kind}\n{sanitized_text}\n\n")
    
    return file_path

def read_ledger(date_str: str, root: Path | None = None) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        raise ValueError("Invalid date format")
    try:
        date.fromisoformat(date_str)
    except ValueError:
        raise ValueError("Invalid calendar date")
        
    path = get_ledger_dir(root) / f"{date_str}.md"
    if not path.exists():
        return ""
    return path.read_text("utf-8", errors="replace")

def search_ledger(query: str, days: int = 30, root: Path | None = None, today: date | None = None) -> list[dict]:
    query = query.strip()
    if not query:
        raise ValueError("Empty query")
    if not (1 <= days <= 365):
        raise ValueError("Days must be 1..365")
    
    today = today or date.today()
    ledger_dir = get_ledger_dir(root)
    results = []
    
    q_lower = query.casefold()
    
    for i in range(days):
        day = today - timedelta(days=i)
        path = ledger_dir / f"{day.isoformat()}.md"
        if path.exists():
            try:
                content = path.read_text("utf-8", errors="replace")
                for line in content.splitlines():
                    if q_lower in line.casefold():
                        results.append({"date": day.isoformat(), "line": line.strip()})
                        if len(results) >= 200:
                            return results
            except OSError:
                continue
    return results

class LedgerSearchResponse(BaseModel):
    matches: list[dict]

class LedgerReadResponse(BaseModel):
    date: str
    text: str

def build_ledger_router(root: Path | None = None) -> APIRouter:
    router = APIRouter()

    @router.get("/ledger/search", response_model=LedgerSearchResponse)
    def search(q: str = Query(..., min_length=1, max_length=200), days: int = Query(30, ge=1, le=365)):
        try:
            return {"matches": search_ledger(q, days, root=root)}
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.get("/ledger/{date_str}", response_model=LedgerReadResponse)
    def read(date_str: str):
        try:
            return {"date": date_str, "text": read_ledger(date_str, root=root)}
        except ValueError as e:
            raise HTTPException(422, str(e))

    return router
