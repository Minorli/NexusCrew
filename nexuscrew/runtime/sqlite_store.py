"""SQLite-backed durable runtime state."""
import json
import sqlite3
from pathlib import Path


class DurableStateStore:
    """SQLite store for enterprise-grade runtime state."""

    def __init__(self, path: Path):
        # Enterprise phase: unify durable state in SQLite.
        self.path = path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    task_id TEXT,
                    type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    run_id TEXT NOT NULL,
                    hop INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    current_agent TEXT NOT NULL,
                    current_message TEXT NOT NULL,
                    history_json TEXT NOT NULL,
                    dev_retries INTEGER NOT NULL,
                    task_status TEXT NOT NULL,
                    metrics_summary TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    PRIMARY KEY (run_id, hop)
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS background_runs (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    chat_id INTEGER NOT NULL DEFAULT 0,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    chat_id INTEGER NOT NULL,
                    id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_to TEXT NOT NULL,
                    branch_name TEXT NOT NULL,
                    github_issue_number INTEGER NOT NULL,
                    github_issue_url TEXT NOT NULL,
                    github_pr_number INTEGER NOT NULL,
                    github_pr_url TEXT NOT NULL,
                    slack_channel TEXT NOT NULL,
                    slack_message_ts TEXT NOT NULL,
                    slack_thread_ts TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    history_json TEXT NOT NULL,
                    PRIMARY KEY (chat_id, id)
                );

                CREATE TABLE IF NOT EXISTS webhook_deliveries (
                    provider TEXT NOT NULL,
                    delivery_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    PRIMARY KEY (provider, delivery_id)
                );
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(background_runs)").fetchall()
            }
            if "chat_id" not in columns:
                conn.execute(
                    "ALTER TABLE background_runs ADD COLUMN chat_id INTEGER NOT NULL DEFAULT 0"
                )

    def append_event(self, event) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO events
                (id, run_id, chat_id, task_id, type, actor, ts, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.run_id,
                    event.chat_id,
                    event.task_id,
                    event.type,
                    event.actor,
                    event.ts,
                    json.dumps(event.payload, ensure_ascii=False),
                ),
            )

    def list_run_events(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY ts, id",
                (run_id,),
            ).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    def save_checkpoint(self, checkpoint) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints
                (run_id, hop, chat_id, task_id, current_agent, current_message,
                 history_json, dev_retries, task_status, metrics_summary, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.run_id,
                    checkpoint.hop,
                    checkpoint.chat_id,
                    checkpoint.task_id,
                    checkpoint.current_agent,
                    checkpoint.current_message,
                    json.dumps(checkpoint.history, ensure_ascii=False),
                    checkpoint.dev_retries,
                    checkpoint.task_status,
                    checkpoint.metrics_summary,
                    checkpoint.ts,
                ),
            )

    def load_latest_checkpoint(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM checkpoints
                WHERE run_id = ?
                ORDER BY hop DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["history"] = json.loads(record.pop("history_json"))
        return record

    def save_approval(self, approval) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO approvals
                (id, action_type, risk_level, summary, payload_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.id,
                    approval.action_type,
                    approval.risk_level,
                    approval.summary,
                    json.dumps(approval.payload, ensure_ascii=False),
                    approval.status,
                    approval.created_at,
                    approval.updated_at,
                ),
            )

    def load_approvals(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM approvals ORDER BY created_at, id").fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    def save_background_run(self, run) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO background_runs
                (id, label, status, chat_id, task_id, run_id, created_at, updated_at, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.label,
                    run.status,
                    getattr(run, "chat_id", 0),
                    run.task_id,
                    run.run_id,
                    run.created_at,
                    run.updated_at,
                    run.error,
                ),
            )

    def load_background_runs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM background_runs ORDER BY created_at, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_task(self, chat_id: int, task) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks
                (chat_id, id, description, status, assigned_to, branch_name,
                 github_issue_number, github_issue_url, github_pr_number, github_pr_url,
                 slack_channel, slack_message_ts, slack_thread_ts,
                 created_at, updated_at, history_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    task.id,
                    task.description,
                    task.status.value if hasattr(task.status, "value") else str(task.status),
                    task.assigned_to,
                    task.branch_name,
                    task.github_issue_number,
                    task.github_issue_url,
                    task.github_pr_number,
                    task.github_pr_url,
                    getattr(task, "slack_channel", ""),
                    getattr(task, "slack_message_ts", ""),
                    getattr(task, "slack_thread_ts", ""),
                    task.created_at,
                    task.updated_at,
                    json.dumps(task.history, ensure_ascii=False),
                ),
            )

    def load_tasks(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY chat_id, id"
            ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            record["history"] = json.loads(record.pop("history_json"))
            records.append(record)
        return records

    def has_webhook_delivery(self, provider: str, delivery_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM webhook_deliveries WHERE provider = ? AND delivery_id = ?",
                (provider, delivery_id),
            ).fetchone()
        return row is not None

    def save_webhook_delivery(self, provider: str, delivery_id: str, event_type: str, received_at: str):
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO webhook_deliveries
                (provider, delivery_id, event_type, received_at)
                VALUES (?, ?, ?, ?)
                """,
                (provider, delivery_id, event_type, received_at),
            )
