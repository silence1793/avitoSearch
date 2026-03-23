import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Task:
    id: int
    chat_id: int
    text: str
    done: int
    created_ts: int


@dataclass
class Reminder:
    id: int
    task_id: int
    chat_id: int
    remind_at_ts: int
    sent: int


@dataclass
class Wish:
    id: int
    chat_id: int
    item: str
    budget: Optional[int]
    found: int
    created_ts: int


@dataclass
class FinanceEntry:
    id: int
    chat_id: int
    amount: int
    category: str
    note: str
    created_ts: int


@dataclass
class ContentItem:
    id: int
    chat_id: int
    kind: str
    title: str
    body: str
    done: int
    created_ts: int


class Store:
    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        cur = self.conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                created_ts INTEGER NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                remind_at_ts INTEGER NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS wishes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                item TEXT NOT NULL,
                budget INTEGER,
                found INTEGER NOT NULL DEFAULT 0,
                created_ts INTEGER NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                category TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_ts INTEGER NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                chat_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(chat_id, key)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS focus_checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                day_key TEXT NOT NULL,
                item1 TEXT NOT NULL,
                item2 TEXT NOT NULL,
                item3 TEXT NOT NULL,
                done1 INTEGER NOT NULL DEFAULT 0,
                done2 INTEGER NOT NULL DEFAULT 0,
                done3 INTEGER NOT NULL DEFAULT 0,
                created_ts INTEGER NOT NULL,
                UNIQUE(chat_id, day_key)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS content_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                created_ts INTEGER NOT NULL
            )
            """
        )

        self.conn.commit()

    # Tasks
    def add_task(self, chat_id: int, text: str) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO tasks(chat_id, text, done, created_ts) VALUES (?, ?, 0, ?)",
            (chat_id, text.strip(), int(time.time())),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_tasks(self, chat_id: int, include_done: bool = False) -> List[Task]:
        cur = self.conn.cursor()
        if include_done:
            cur.execute(
                "SELECT id, chat_id, text, done, created_ts FROM tasks WHERE chat_id = ? ORDER BY id DESC",
                (chat_id,),
            )
        else:
            cur.execute(
                "SELECT id, chat_id, text, done, created_ts FROM tasks WHERE chat_id = ? AND done = 0 ORDER BY id DESC",
                (chat_id,),
            )
        rows = cur.fetchall()
        return [Task(**dict(r)) for r in rows]

    def mark_done(self, chat_id: int, task_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("UPDATE tasks SET done = 1 WHERE chat_id = ? AND id = ?", (chat_id, task_id))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_task(self, chat_id: int, task_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM reminders WHERE chat_id = ? AND task_id = ?", (chat_id, task_id))
        cur.execute("DELETE FROM tasks WHERE chat_id = ? AND id = ?", (chat_id, task_id))
        self.conn.commit()
        return cur.rowcount > 0

    def get_task(self, chat_id: int, task_id: int) -> Optional[Task]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, chat_id, text, done, created_ts FROM tasks WHERE chat_id = ? AND id = ?",
            (chat_id, task_id),
        )
        row = cur.fetchone()
        return Task(**dict(row)) if row else None

    # Reminders
    def add_reminder(self, chat_id: int, task_id: int, remind_at_ts: int) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO reminders(task_id, chat_id, remind_at_ts, sent) VALUES (?, ?, ?, 0)",
            (task_id, chat_id, remind_at_ts),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def due_reminders(self, now_ts: int) -> List[Reminder]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id, task_id, chat_id, remind_at_ts, sent
            FROM reminders
            WHERE sent = 0 AND remind_at_ts <= ?
            ORDER BY remind_at_ts ASC
            """,
            (now_ts,),
        )
        rows = cur.fetchall()
        return [Reminder(**dict(r)) for r in rows]

    def mark_reminder_sent(self, reminder_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
        self.conn.commit()

    # Wishes / shopping
    def add_wish(self, chat_id: int, item: str, budget: Optional[int]) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO wishes(chat_id, item, budget, found, created_ts) VALUES (?, ?, ?, 0, ?)",
            (chat_id, item.strip(), budget, int(time.time())),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_wishes(self, chat_id: int, include_found: bool = False) -> List[Wish]:
        cur = self.conn.cursor()
        if include_found:
            cur.execute(
                "SELECT id, chat_id, item, budget, found, created_ts FROM wishes WHERE chat_id = ? ORDER BY id DESC",
                (chat_id,),
            )
        else:
            cur.execute(
                """
                SELECT id, chat_id, item, budget, found, created_ts
                FROM wishes
                WHERE chat_id = ? AND found = 0
                ORDER BY id DESC
                """,
                (chat_id,),
            )
        rows = cur.fetchall()
        return [Wish(**dict(r)) for r in rows]

    def mark_wish_found(self, chat_id: int, wish_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("UPDATE wishes SET found = 1 WHERE chat_id = ? AND id = ?", (chat_id, wish_id))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_wish(self, chat_id: int, wish_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM wishes WHERE chat_id = ? AND id = ?", (chat_id, wish_id))
        self.conn.commit()
        return cur.rowcount > 0

    # Finance
    def add_spent(self, chat_id: int, amount: int, category: str, note: str) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO finance_entries(chat_id, amount, category, note, created_ts)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, amount, category.strip().lower(), note.strip(), int(time.time())),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_spent(self, chat_id: int, limit: int = 20) -> List[FinanceEntry]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id, chat_id, amount, category, note, created_ts
            FROM finance_entries
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (chat_id, limit),
        )
        rows = cur.fetchall()
        return [FinanceEntry(**dict(r)) for r in rows]

    def spent_total_for_month(self, chat_id: int, month_start_ts: int, month_end_ts: int) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM finance_entries
            WHERE chat_id = ? AND created_ts >= ? AND created_ts < ?
            """,
            (chat_id, month_start_ts, month_end_ts),
        )
        row = cur.fetchone()
        return int(row["total"]) if row else 0

    # User settings
    def set_setting(self, chat_id: int, key: str, value: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO user_settings(chat_id, key, value)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, key) DO UPDATE SET value = excluded.value
            """,
            (chat_id, key, value),
        )
        self.conn.commit()

    def get_setting(self, chat_id: int, key: str, default: Optional[str] = None) -> Optional[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM user_settings WHERE chat_id = ? AND key = ?", (chat_id, key))
        row = cur.fetchone()
        return row["value"] if row else default

    # Focus
    def upsert_focus_day(self, chat_id: int, day_key: str, item1: str, item2: str, item3: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO focus_checkins(chat_id, day_key, item1, item2, item3, done1, done2, done3, created_ts)
            VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?)
            ON CONFLICT(chat_id, day_key)
            DO UPDATE SET item1 = excluded.item1, item2 = excluded.item2, item3 = excluded.item3
            """,
            (chat_id, day_key, item1.strip(), item2.strip(), item3.strip(), int(time.time())),
        )
        self.conn.commit()

    def get_focus_day(self, chat_id: int, day_key: str):
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id, chat_id, day_key, item1, item2, item3, done1, done2, done3, created_ts
            FROM focus_checkins
            WHERE chat_id = ? AND day_key = ?
            """,
            (chat_id, day_key),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def set_focus_done(self, chat_id: int, day_key: str, idx: int) -> bool:
        if idx not in (1, 2, 3):
            return False
        cur = self.conn.cursor()
        cur.execute(
            f"UPDATE focus_checkins SET done{idx} = 1 WHERE chat_id = ? AND day_key = ?",
            (chat_id, day_key),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # Content
    def add_content(self, chat_id: int, kind: str, title: str, body: str) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO content_items(chat_id, kind, title, body, done, created_ts)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (chat_id, kind, title.strip(), body.strip(), int(time.time())),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_content(self, chat_id: int, kind: Optional[str] = None, include_done: bool = False) -> List[ContentItem]:
        cur = self.conn.cursor()
        if kind and include_done:
            cur.execute(
                """
                SELECT id, chat_id, kind, title, body, done, created_ts
                FROM content_items
                WHERE chat_id = ? AND kind = ?
                ORDER BY id DESC
                """,
                (chat_id, kind),
            )
        elif kind:
            cur.execute(
                """
                SELECT id, chat_id, kind, title, body, done, created_ts
                FROM content_items
                WHERE chat_id = ? AND kind = ? AND done = 0
                ORDER BY id DESC
                """,
                (chat_id, kind),
            )
        elif include_done:
            cur.execute(
                """
                SELECT id, chat_id, kind, title, body, done, created_ts
                FROM content_items
                WHERE chat_id = ?
                ORDER BY id DESC
                """,
                (chat_id,),
            )
        else:
            cur.execute(
                """
                SELECT id, chat_id, kind, title, body, done, created_ts
                FROM content_items
                WHERE chat_id = ? AND done = 0
                ORDER BY id DESC
                """,
                (chat_id,),
            )
        rows = cur.fetchall()
        return [ContentItem(**dict(r)) for r in rows]

    def mark_content_done(self, chat_id: int, item_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("UPDATE content_items SET done = 1 WHERE chat_id = ? AND id = ?", (chat_id, item_id))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_content(self, chat_id: int, item_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM content_items WHERE chat_id = ? AND id = ?", (chat_id, item_id))
        self.conn.commit()
        return cur.rowcount > 0
