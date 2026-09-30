"""SQLite persistence. One connection, guarded by a lock; adequate for a local single-user tool."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL,
    last_used   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    request     TEXT NOT NULL,
    status      TEXT NOT NULL,              -- queued|running|completed|failed|cancelled
    stage       TEXT,
    model       TEXT NOT NULL,
    branch      TEXT,
    plan        TEXT,
    analysis    TEXT,
    review      TEXT,
    summary     TEXT,
    test_status TEXT,                       -- passed|failed|skipped|unknown
    error       TEXT,
    created_at  REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id   INTEGER NOT NULL,
    seq       INTEGER NOT NULL DEFAULT 0,
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,
    stage     TEXT,
    message   TEXT NOT NULL,
    data      TEXT
);
CREATE TABLE IF NOT EXISTS file_changes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id   INTEGER NOT NULL,
    path      TEXT NOT NULL,
    action    TEXT NOT NULL,                -- created|modified|deleted
    stage     TEXT,
    added     INTEGER DEFAULT 0,
    removed   INTEGER DEFAULT 0,
    ts        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    stage       TEXT,
    command     TEXT NOT NULL,
    cwd         TEXT,
    exit_code   INTEGER,
    stdout      TEXT,
    stderr      TEXT,
    duration_ms INTEGER,
    confirmed   INTEGER DEFAULT 0,
    ts          REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id   INTEGER NOT NULL,
    name      TEXT NOT NULL,
    role      TEXT NOT NULL,
    status    TEXT NOT NULL,                -- running|done|failed|skipped
    output    TEXT,
    started   REAL NOT NULL,
    finished  REAL,
    in_tokens  INTEGER DEFAULT 0,
    out_tokens INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.commit()

    # --- low level -----------------------------------------------------
    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def _row(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        rows = self._rows(sql, args)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- projects ------------------------------------------------------
    def upsert_project(self, name: str, path: str) -> dict[str, Any]:
        now = time.time()
        existing = self._row("SELECT * FROM projects WHERE path = ?", (path,))
        if existing:
            self._exec("UPDATE projects SET last_used = ?, name = ? WHERE id = ?",
                       (now, name, existing["id"]))
            return self.get_project(existing["id"])  # type: ignore[return-value]
        cur = self._exec(
            "INSERT INTO projects (name, path, created_at, last_used) VALUES (?,?,?,?)",
            (name, path, now, now))
        return self.get_project(int(cur.lastrowid))  # type: ignore[return-value]

    def list_projects(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM projects ORDER BY last_used DESC")

    def get_project(self, pid: int) -> dict[str, Any] | None:
        return self._row("SELECT * FROM projects WHERE id = ?", (pid,))

    def get_project_by_path(self, path: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM projects WHERE path = ?", (path,))

    def touch_project(self, pid: int) -> None:
        self._exec("UPDATE projects SET last_used = ? WHERE id = ?", (time.time(), pid))

    def delete_project(self, pid: int) -> None:
        self._exec("DELETE FROM projects WHERE id = ?", (pid,))

    # --- tasks ---------------------------------------------------------
    def create_task(self, project_id: int, request: str, model: str,
                    branch: str | None) -> int:
        cur = self._exec(
            "INSERT INTO tasks (project_id, request, status, model, branch, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (project_id, request, "queued", model, branch, time.time()))
        return int(cur.lastrowid)

    def update_task(self, task_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        return self._row(
            "SELECT t.*, p.name AS project_name, p.path AS project_path"
            " FROM tasks t JOIN projects p ON p.id = t.project_id WHERE t.id = ?",
            (task_id,))

    def list_tasks(self, project_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if project_id is None:
            return self._rows(
                "SELECT t.*, p.name AS project_name FROM tasks t"
                " JOIN projects p ON p.id = t.project_id"
                " ORDER BY t.created_at DESC LIMIT ?", (limit,))
        return self._rows(
            "SELECT t.*, p.name AS project_name FROM tasks t"
            " JOIN projects p ON p.id = t.project_id WHERE t.project_id = ?"
            " ORDER BY t.created_at DESC LIMIT ?", (project_id, limit))

    def running_tasks(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM tasks WHERE status IN ('queued','running')")

    # --- events --------------------------------------------------------
    def add_event(self, task_id: int, ts: float, kind: str, stage: str | None,
                  message: str, data: dict | None, seq: int = 0) -> None:
        self._exec("INSERT INTO events (task_id, seq, ts, kind, stage, message, data)"
                   " VALUES (?,?,?,?,?,?,?)",
                   (task_id, seq, ts, kind, stage, message,
                    json.dumps(data) if data else None))

    def get_events(self, task_id: int) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM events WHERE task_id = ? ORDER BY id", (task_id,))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else {}
        return rows

    # --- artefacts -----------------------------------------------------
    def add_file_change(self, task_id: int, path: str, action: str, stage: str | None,
                        added: int = 0, removed: int = 0) -> None:
        self._exec("INSERT INTO file_changes (task_id, path, action, stage, added, removed, ts)"
                   " VALUES (?,?,?,?,?,?,?)",
                   (task_id, path, action, stage, added, removed, time.time()))

    def get_file_changes(self, task_id: int) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM file_changes WHERE task_id = ? ORDER BY id", (task_id,))

    def add_command(self, task_id: int, stage: str | None, command: str, cwd: str,
                    exit_code: int | None, stdout: str, stderr: str,
                    duration_ms: int, confirmed: bool) -> int:
        cur = self._exec(
            "INSERT INTO commands (task_id, stage, command, cwd, exit_code, stdout, stderr,"
            " duration_ms, confirmed, ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (task_id, stage, command, cwd, exit_code, stdout, stderr,
             duration_ms, int(confirmed), time.time()))
        return int(cur.lastrowid)

    def get_commands(self, task_id: int) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM commands WHERE task_id = ? ORDER BY id", (task_id,))

    def start_stage(self, task_id: int, name: str, role: str) -> int:
        cur = self._exec(
            "INSERT INTO stages (task_id, name, role, status, started) VALUES (?,?,?,?,?)",
            (task_id, name, role, "running", time.time()))
        return int(cur.lastrowid)

    def finish_stage(self, stage_id: int, status: str, output: str | None = None,
                     in_tokens: int = 0, out_tokens: int = 0) -> None:
        self._exec(
            "UPDATE stages SET status = ?, output = ?, finished = ?, in_tokens = ?,"
            " out_tokens = ? WHERE id = ?",
            (status, output, time.time(), in_tokens, out_tokens, stage_id))

    def get_stages(self, task_id: int) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM stages WHERE task_id = ? ORDER BY id", (task_id,))

    def merged_file_changes(self, task_id: int) -> list[dict[str, Any]]:
        """One row per path: the audit log collapsed into what actually happened.

        A file created and then edited in the same task reads as `created`; one
        edited and then deleted reads as `deleted`.
        """
        merged: dict[str, dict[str, Any]] = {}
        for row in self.get_file_changes(task_id):
            prior = merged.get(row["path"])
            if prior is None:
                merged[row["path"]] = {"path": row["path"], "action": row["action"],
                                       "added": row["added"], "removed": row["removed"]}
                continue
            prior["added"] += row["added"]
            prior["removed"] += row["removed"]
            if row["action"] == "deleted":
                prior["action"] = "deleted"
            elif prior["action"] != "created":
                prior["action"] = row["action"]
        return sorted(merged.values(), key=lambda r: r["path"])

    def task_detail(self, task_id: int) -> dict[str, Any] | None:
        task = self.get_task(task_id)
        if not task:
            return None
        task["stages"] = self.get_stages(task_id)
        task["files"] = self.merged_file_changes(task_id)
        task["file_log"] = self.get_file_changes(task_id)
        task["commands"] = self.get_commands(task_id)
        task["events"] = self.get_events(task_id)
        return task
