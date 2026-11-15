"""Durable store behind Buddy: alerts, approvals, notification history, tasks, agents,
connections, prefs and activity. stdlib sqlite3 only (same WAL + integrity-check pattern as
work_store.py, but one long-lived connection because Buddy writes on every poll).

Rules the rest of Core relies on:
  * approvals are NEVER replayed: on open, anything still undecided becomes 'expired'
  * a corrupt file is moved aside to <name>.corrupt-<n> and a fresh DB is started
  * every table is bounded (row cap + age) so a chatty agent cannot fill the disk
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("uvicorn.error")

DEFAULT_RETENTION_DAYS = 30
ROW_CAPS = {"alerts": 500, "approvals": 500, "notifications": 1000, "tasks": 200,
            "connections": 500, "activity": 500, "agents": 50}
TS_COL = {"alerts": "created", "approvals": "created", "notifications": "ts", "tasks": "updated",
          "connections": "ts", "activity": "ts", "agents": "last_seen"}
CONNECTION_SAMPLE_S = 30   # one connection row per client per 30 s, not one per poll
PRUNE_EVERY = 50           # writes between retention sweeps

# Append-only: migration N takes the DB from version N-1 to N. Never edit an applied entry.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE alerts (id TEXT PRIMARY KEY, idem TEXT UNIQUE, agent TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL, data TEXT NOT NULL, outcome TEXT, outcome_at REAL, expires REAL);
    CREATE TABLE approvals (id TEXT PRIMARY KEY, idem TEXT UNIQUE, agent TEXT NOT NULL DEFAULT '',
        tool TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '', sensitive INTEGER NOT NULL DEFAULT 0,
        created REAL NOT NULL, decision TEXT, decided_at REAL, decided_by TEXT, reason TEXT);
    CREATE TABLE notifications (id INTEGER PRIMARY KEY, idem TEXT UNIQUE, kind TEXT NOT NULL,
        text TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', agent TEXT NOT NULL DEFAULT '',
        ts REAL NOT NULL, data TEXT);
    CREATE TABLE tasks (id TEXT PRIMARY KEY, agent TEXT NOT NULL DEFAULT '', title TEXT NOT NULL,
        status TEXT NOT NULL, progress INTEGER, detail TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL, updated REAL NOT NULL);
    CREATE TABLE agents (name TEXT PRIMARY KEY, friendly_name TEXT NOT NULL DEFAULT '',
        client_kind TEXT NOT NULL DEFAULT '', capabilities TEXT NOT NULL DEFAULT '[]',
        first_seen REAL NOT NULL, last_seen REAL NOT NULL);
    CREATE TABLE connections (id INTEGER PRIMARY KEY AUTOINCREMENT, client TEXT NOT NULL,
        kind TEXT NOT NULL, ts REAL NOT NULL);
    CREATE TABLE prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE activity (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
        level TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL);
    CREATE INDEX approvals_created ON approvals(created);
    CREATE INDEX connections_ts ON connections(ts);
    """,
    """
    ALTER TABLE approvals ADD COLUMN digest TEXT NOT NULL DEFAULT '';
    ALTER TABLE approvals ADD COLUMN instruction TEXT NOT NULL DEFAULT '';
    """,
]
SCHEMA_VERSION = len(MIGRATIONS)


class BuddyStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._writes = 0
        self._last_conn: dict[str, float] = {}
        self.recovered_from: str | None = None   # set when a corrupt file was moved aside
        self.db = self._open()
        self.expired_on_start = self.expire_pending_approvals()

    # ---- open / recover / migrate
    def _open(self) -> sqlite3.Connection:
        try:
            return self._connect()
        except sqlite3.DatabaseError as error:
            moved = self._quarantine()
            log.error("buddy store unreadable (%s); moved to %s, starting fresh", error, moved)
            self.recovered_from = moved.name
            db = self._connect()
            self._activity(db, "error", "store_recovered", f"corrupt database moved to {moved.name}")
            return db

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA journal_mode=WAL")
            integrity = [r[0] for r in db.execute("PRAGMA integrity_check").fetchall()]
            if integrity != ["ok"]:
                raise sqlite3.DatabaseError("integrity_check: " + "; ".join(map(str, integrity)))
            db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = row[0] or 0
            if current > SCHEMA_VERSION:
                raise sqlite3.DatabaseError(f"database schema {current} is newer than this Core ({SCHEMA_VERSION})")
            for version in range(current + 1, SCHEMA_VERSION + 1):
                db.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in MIGRATIONS[version - 1].split(";"):
                        if stmt.strip():
                            db.execute(stmt)
                    db.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
                    db.execute("COMMIT")
                except Exception:
                    db.execute("ROLLBACK")
                    raise
            return db
        except Exception:
            db.close()
            raise

    def _quarantine(self) -> Path:
        n = 1
        while self.path.with_name(f"{self.path.name}.corrupt-{n}").exists():
            n += 1
        target = self.path.with_name(f"{self.path.name}.corrupt-{n}")
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(self.path) + suffix)
            if src.exists():
                src.replace(Path(str(target) + suffix))
        return target

    def close(self) -> None:
        with self._lock:
            self.db.close()

    # ---- helpers
    def _run(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.db.execute(sql, args)
            self._writes += 1
            if self._writes % PRUNE_EVERY == 0:
                self._prune_locked()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    @staticmethod
    def _activity(db: sqlite3.Connection, level: str, kind: str, message: str) -> None:
        db.execute("INSERT INTO activity(ts, level, kind, message) VALUES (?,?,?,?)",
                   (time.time(), level, kind, message[:300]))

    def log_activity(self, level: str, kind: str, message: str) -> None:
        self._run("INSERT INTO activity(ts, level, kind, message) VALUES (?,?,?,?)",
                  (time.time(), level, kind, message[:300]))

    # ---- retention
    def retention_days(self) -> int:
        return int(self.get_pref("retention_days", DEFAULT_RETENTION_DAYS))

    def prune(self) -> None:
        with self._lock:
            self._prune_locked()

    def _prune_locked(self) -> None:
        cutoff = time.time() - self.retention_days() * 86400
        for table, cap in ROW_CAPS.items():
            col = TS_COL[table]
            key = "name" if table == "agents" else "rowid"
            extra = " AND decision IS NOT NULL" if table == "approvals" else ""
            self.db.execute(f"DELETE FROM {table} WHERE {col} < ?{extra}", (cutoff,))
            self.db.execute(f"DELETE FROM {table} WHERE {key} IN "
                            f"(SELECT {key} FROM {table} ORDER BY {col} DESC LIMIT -1 OFFSET ?)", (cap,))

    # ---- approvals
    def expire_pending_approvals(self) -> int:
        """Called on open: an approval nobody answered before the restart is dead, never replayed."""
        cur = self._run("UPDATE approvals SET decision='expired', decided_at=?, decided_by='restart', "
                        "reason='core_restarted' WHERE decision IS NULL", (time.time(),))
        if cur.rowcount:
            self.log_activity("warn", "approvals_expired", f"{cur.rowcount} pending approval(s) expired on restart")
        return cur.rowcount

    def find_approval_by_idem(self, idem: str) -> dict | None:
        rows = self._all("SELECT * FROM approvals WHERE idem=?", (idem,))
        return rows[0] if rows else None

    def add_approval(self, item: dict, idem: str | None = None) -> None:
        self._run("INSERT INTO approvals(id, idem, agent, tool, summary, project, session_id, sensitive, "
                  "created, decision, decided_at, decided_by, reason, digest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (item["id"], idem, item.get("agent", ""), item["tool"], item.get("summary", ""),
                   item.get("project", ""), item.get("session_id", ""), int(bool(item.get("sensitive"))),
                   item["t"], item.get("decision"), time.time() if item.get("decision") else None,
                   "auto" if item.get("decision") else None, item.get("reason"), item.get("digest", "")))

    def set_approval_decision(self, rid: str, decision: str, by: str = "", reason: str | None = None, instruction: str = "") -> None:
        # Only an undecided row may change: the first answer wins, also across a restart.
        self._run("UPDATE approvals SET decision=?, decided_at=?, decided_by=?, reason=COALESCE(?, reason), instruction=? "
                  "WHERE id=? AND decision IS NULL",
                  (decision, time.time(), by, reason, instruction, rid))

    def undo_approval(self, rid: str, by: str) -> None:
        self._run("UPDATE approvals SET decision='undone', decided_at=?, decided_by=? WHERE id=? AND decision='allow'",
                  (time.time(), by, rid))

    def approval_row(self, rid: str) -> dict | None:
        rows = self._all("SELECT * FROM approvals WHERE id=?", (rid,))
        return rows[0] if rows else None

    def approvals(self, limit: int = 100) -> list[dict]:
        return self._all("SELECT * FROM approvals ORDER BY created DESC LIMIT ?", (limit,))

    # ---- alerts
    def find_alert_by_idem(self, idem: str) -> dict | None:
        rows = self._all("SELECT * FROM alerts WHERE idem=?", (idem,))
        return rows[0] if rows else None

    def add_alert(self, rec: dict, idem: str | None = None) -> None:
        self._run("INSERT INTO alerts(id, idem, agent, created, data, outcome, outcome_at, expires) "
                  "VALUES (?,?,?,?,?,NULL,NULL,?)",
                  (rec["id"], idem, rec.get("agent", ""), rec["created"], json.dumps(rec["public"]),
                   rec.get("_exp")))

    def set_alert_outcome(self, aid: str, outcome: str, snooze_until: float | None = None) -> None:
        self._run("UPDATE alerts SET outcome=?, outcome_at=?, data=json_set(data, '$._snooze', ?) WHERE id=?",
                  (outcome, time.time(), snooze_until, aid))

    def pending_alerts(self) -> list[dict]:
        """Reminders that never fired, however old: they must survive a flood of newer alerts."""
        rows = self._all("SELECT * FROM alerts WHERE outcome IS NULL AND json_extract(data,'$._due') IS NOT NULL "
                         "AND json_extract(data,'$._fired') IS NULL")
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    def mark_alert_fired(self, aid: str) -> None:
        self._run("UPDATE alerts SET data=json_set(data, '$._fired', json('true')) WHERE id=?", (aid,))

    def alerts(self, limit: int = 100) -> list[dict]:
        rows = self._all("SELECT * FROM alerts ORDER BY created DESC LIMIT ?", (limit,))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    # ---- notifications (the /buddy/events ring, durable; its id IS the event id)
    def max_event_id(self) -> int:
        # The notifications table is bounded and may become empty after retention.
        # The cursor cannot move backwards or devices polling with since=old_id
        # will silently miss subsequent events after a Core restart.
        with self._lock:
            retained = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM notifications").fetchone()[0]
            high_water = self.get_pref("_event_cursor", 0)
            return max(retained, int(high_water))

    def record_event_id(self, event_id: int) -> None:
        """Persist an event ID independently from expiring notification history."""
        with self._lock:
            prev = int(self.get_pref("_event_cursor", 0))
            if event_id > prev:
                self.set_pref("_event_cursor", event_id)

    def add_notification(self, ev: dict, agent: str = "", idem: str | None = None) -> None:
        extra = {k: v for k, v in ev.items() if k not in ("id", "kind", "text", "source", "ts")}
        self._run("INSERT INTO notifications(id, idem, kind, text, source, agent, ts, data) VALUES (?,?,?,?,?,?,?,?)",
                  (ev["id"], idem, ev["kind"], ev["text"], ev.get("source", ""), agent, ev["ts"],
                   json.dumps(extra) if extra else None))

    def find_notification_by_idem(self, idem: str) -> dict | None:
        rows = self._all("SELECT * FROM notifications WHERE idem=?", (idem,))
        return rows[0] if rows else None

    def notifications(self, limit: int = 100) -> list[dict]:
        return self._all("SELECT id, kind, text, source, agent, ts FROM notifications ORDER BY id DESC LIMIT ?", (limit,))

    # ---- tasks
    def upsert_task(self, tid: str, agent: str, title: str, status: str, progress: int | None, detail: str) -> dict:
        now = time.time()
        self._run("INSERT INTO tasks(id, agent, title, status, progress, detail, created, updated) "
                  "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                  "progress=COALESCE(excluded.progress, progress), detail=excluded.detail, updated=excluded.updated, "
                  "title=excluded.title", (tid, agent, title, status, progress, detail, now, now))
        return self._all("SELECT * FROM tasks WHERE id=?", (tid,))[0]

    def get_task(self, tid: str) -> dict | None:
        rows = self._all("SELECT * FROM tasks WHERE id=?", (tid,))
        return rows[0] if rows else None

    def tasks(self, limit: int = 100) -> list[dict]:
        return self._all("SELECT * FROM tasks ORDER BY updated DESC LIMIT ?", (limit,))

    # ---- agents + connections
    def touch_agent(self, name: str, client_kind: str = "", capabilities: list[str] | None = None,
                    friendly_name: str = "") -> None:
        now = time.time()
        self._run("INSERT INTO agents(name, friendly_name, client_kind, capabilities, first_seen, last_seen) "
                  "VALUES (?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen, "
                  "client_kind=CASE WHEN excluded.client_kind<>'' THEN excluded.client_kind ELSE client_kind END, "
                  "friendly_name=CASE WHEN excluded.friendly_name<>'' THEN excluded.friendly_name ELSE friendly_name END, "
                  "capabilities=CASE WHEN excluded.capabilities<>'[]' THEN excluded.capabilities ELSE capabilities END",
                  (name, friendly_name, client_kind, json.dumps(capabilities or []), now, now))

    def agents(self) -> list[dict]:
        rows = self._all("SELECT * FROM agents ORDER BY last_seen DESC")
        for r in rows:
            r["capabilities"] = json.loads(r["capabilities"])
        return rows

    def record_connection(self, client: str, kind: str) -> None:
        now = time.time()
        if now - self._last_conn.get(client, 0) < CONNECTION_SAMPLE_S:
            return
        self._last_conn[client] = now
        self._run("INSERT INTO connections(client, kind, ts) VALUES (?,?,?)", (client, kind, now))

    def connections(self, limit: int = 100) -> list[dict]:
        return self._all("SELECT client, kind, ts FROM connections ORDER BY ts DESC LIMIT ?", (limit,))

    # ---- prefs / shortcuts
    def get_pref(self, key: str, default: Any = None) -> Any:
        rows = self._all("SELECT value FROM prefs WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_pref(self, key: str, value: Any) -> None:
        self._run("INSERT INTO prefs(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value)))

    def prefs(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self._all("SELECT key, value FROM prefs")}

    def activity(self, limit: int = 100) -> list[dict]:
        return self._all("SELECT ts, level, kind, message FROM activity ORDER BY id DESC LIMIT ?", (limit,))

    # ---- export / reset
    def export(self) -> dict:
        """Everything the companion shows, as JSON. No token, no secrets: none are ever stored here."""
        return {"schema_version": SCHEMA_VERSION, "exported_at": time.time(), "prefs": self.prefs(),
                "agents": self.agents(), "tasks": self.tasks(ROW_CAPS["tasks"]),
                "approvals": self.approvals(ROW_CAPS["approvals"]),
                "alerts": self.alerts(ROW_CAPS["alerts"]),
                "notifications": self.notifications(ROW_CAPS["notifications"]),
                "connections": self.connections(ROW_CAPS["connections"]),
                "activity": self.activity(ROW_CAPS["activity"])}

    def reset(self) -> None:
        """Delete saved history. Prefs and shortcuts survive; the event id counter does not restart."""
        with self._lock:
            for table in ("alerts", "approvals", "tasks", "agents", "connections", "activity"):
                self.db.execute(f"DELETE FROM {table}")
            # Keep the newest notification row only as the id high-water mark, so a polling device's
            # `since` cursor can never end up ahead of Core's counter.
            self.db.execute("DELETE FROM notifications WHERE id < (SELECT MAX(id) FROM notifications)")
            self.db.execute("UPDATE notifications SET text='(cleared)', source='', agent='', data=NULL, idem=NULL")
            self.log_activity("info", "reset", "saved history cleared")
