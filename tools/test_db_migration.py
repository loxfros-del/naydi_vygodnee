"""Проверка миграции requests для старой SQLite-схемы."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")

from app import db  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "old.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT DEFAULT '',
                product TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                use_case TEXT DEFAULT '',
                budget TEXT DEFAULT '',
                city TEXT DEFAULT '',
                important_criteria TEXT DEFAULT '',
                clean_search_query TEXT DEFAULT '',
                is_used_allowed INTEGER DEFAULT 0,
                original_query TEXT DEFAULT '',
                status TEXT DEFAULT 'NEW',
                found_products TEXT DEFAULT '[]',
                search_links TEXT DEFAULT '[]',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )"""
        )
        conn.execute("INSERT INTO requests (user_id, status) VALUES (1, 'PAID')")
        conn.commit()
        conn.close()

        db.settings.DB_PATH = db_path
        db.init_db()
        db.update_request(1, preview_text="preview", report_text="report", alice_response="alice")
        req = db.get_request(1)

        assert req is not None
        assert req.preview_text == "preview"
        assert req.report_text == "report"
        assert req.alice_response == "alice"

    print("test_db_migration: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
