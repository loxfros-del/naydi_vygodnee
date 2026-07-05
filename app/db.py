"""Работа с SQLite: модели, создание таблиц, CRUD."""
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.config import settings
from app.link_checks import LinkCheckStatus, normalize_link_check_status

def to_int_price(value) -> Optional[int]:
    """Безопасно превращает цену из строки/числа в int."""
    if value is None:
        return None

    if isinstance(value, int):
        return value

    text = str(value)
    digits = "".join(ch for ch in text if ch.isdigit())

    if not digits:
        return None

    return int(digits)

# ──────────────────────────────────────────────
#  Модели данных
# ──────────────────────────────────────────────

@dataclass
class Request:
    id: int
    user_id: int
    username: str = ""
    product: str = ""
    product_name: str = ""
    use_case: str = ""
    purpose: str = ""
    criteria: str = ""
    budget: str = ""
    city: str = ""
    important_criteria: str = ""
    clean_search_query: str = ""
    is_used_allowed: bool = False
    original_query: str = ""
    status: str = "NEW"
    found_products: str = "[]"
    preview_text: str = ""
    report_text: str = ""
    alice_response: str = ""
    created_at: str = ""
    search_links: str = "[]"
    updated_at: str = ""


@dataclass
class SearchResult:
    id: int
    request_id: int
    title: str = ""
    price: Optional[int] = None
    source: str = ""
    url: str = ""
    snippet: str = ""
    score: float = 0.0
    risk_flags: str = "[]"
    status: str = "CANDIDATE"
    admin_note: str = ""
    origin: str = "auto"
    sort_order: int = 0
    price_verified: bool = False
    link_check_status: str = LinkCheckStatus.NEEDED.value
    facts_json: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SearchAttempt:
    id: int
    request_id: int
    source: str
    query: str
    status: str
    found_count: int = 0
    kept_count: int = 0
    error_text: str = ""
    created_at: str = ""


@dataclass
class MarketCheck:
    """Запись проверки рынка — самый дешёвый найденный вариант."""
    id: int
    request_id: int
    title: str = ""
    price: Optional[int] = None
    source: str = ""
    url: str = ""
    rating_reviews: str = ""
    city: str = ""
    condition: str = ""
    verdict: str = ""          # "BUY" / "DO_NOT_BUY" / "PROMOTED"
    reason: str = ""           # почему берём или не берём
    promoted_to_card_id: Optional[int] = None  # если добавлен в подборку
    created_at: str = ""
    updated_at: str = ""


# ──────────────────────────────────────────────
#  Подключение к БД
# ──────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _run_migrations(conn: sqlite3.Connection):
    _ensure_column(conn, "requests", "preview_text", "TEXT DEFAULT ''")
    _ensure_column(conn, "requests", "report_text", "TEXT DEFAULT ''")
    _ensure_column(conn, "requests", "alice_response", "TEXT DEFAULT ''")
    _ensure_column(conn, "search_results", "facts_json", "TEXT DEFAULT ''")


def init_db():
    """Создаёт таблицы, если их нет."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS requests (
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
            preview_text TEXT DEFAULT '',
            report_text TEXT DEFAULT '',
            alice_response TEXT DEFAULT '',
            search_links TEXT DEFAULT '[]',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS search_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            title TEXT DEFAULT '',
            price INTEGER,
            source TEXT DEFAULT '',
            url TEXT DEFAULT '',
            snippet TEXT DEFAULT '',
            score REAL DEFAULT 0.0,
            risk_flags TEXT DEFAULT '[]',
            status TEXT DEFAULT 'CANDIDATE',
            admin_note TEXT DEFAULT '',
            origin TEXT DEFAULT 'auto',
            sort_order INTEGER DEFAULT 0,
            price_verified INTEGER DEFAULT 0,
            link_check_status TEXT DEFAULT 'NEEDED',
            facts_json TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            FOREIGN KEY (request_id) REFERENCES requests(id)
        );

        CREATE TABLE IF NOT EXISTS search_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            source TEXT DEFAULT '',
            query TEXT DEFAULT '',
            status TEXT DEFAULT '',
            found_count INTEGER DEFAULT 0,
            kept_count INTEGER DEFAULT 0,
            error_text TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            FOREIGN KEY (request_id) REFERENCES requests(id)
        );

        CREATE TABLE IF NOT EXISTS manual_search_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            source TEXT DEFAULT '',
            query TEXT DEFAULT '',
            url TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            FOREIGN KEY (request_id) REFERENCES requests(id)
        );

        CREATE TABLE IF NOT EXISTS market_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            title TEXT DEFAULT '',
            price INTEGER,
            source TEXT DEFAULT '',
            url TEXT DEFAULT '',
            rating_reviews TEXT DEFAULT '',
            city TEXT DEFAULT '',
            condition TEXT DEFAULT '',
            verdict TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            promoted_to_card_id INTEGER,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            FOREIGN KEY (request_id) REFERENCES requests(id)
        );
    """)
    _run_migrations(conn)
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────
#  CRUD: Заявки
# ──────────────────────────────────────────────

def create_request(user_id: int, username: str = "", product: str = "",
                   product_name: str = "", use_case: str = "", budget: str = "",
                   city: str = "", important_criteria: str = "",
                   clean_search_query: str = "", is_used_allowed: bool = False,
                   original_query: str = "") -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    cur = conn.execute(
        """INSERT INTO requests
           (user_id, username, product, product_name, use_case, budget, city,
            important_criteria, clean_search_query, is_used_allowed,
            original_query, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW', ?, ?)""",
        (user_id, username, product, product_name, use_case, budget, city,
         important_criteria, clean_search_query, int(is_used_allowed),
         original_query, now, now),
    )
    conn.commit()
    req_id = cur.lastrowid
    conn.close()
    return req_id


def _request_from_row(row) -> Optional[Request]:
    if not row:
        return None

    data = dict(row)
    allowed = Request.__dataclass_fields__.keys()
    clean_data = {k: v for k, v in data.items() if k in allowed}
    return Request(**clean_data)


def _search_result_from_row(row) -> Optional[SearchResult]:
    if not row:
        return None

    data = dict(row)
    data["price"] = to_int_price(data.get("price"))

    if "price_verified" in data:
        data["price_verified"] = bool(data.get("price_verified"))

    allowed = SearchResult.__dataclass_fields__.keys()
    clean_data = {k: v for k, v in data.items() if k in allowed}
    return SearchResult(**clean_data)

def get_request(request_id: int) -> Optional[Request]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()
    return _request_from_row(row)

def get_user_active_request(user_id: int) -> Optional[Request]:
    """Возвращает активную заявку пользователя, если она есть."""
    active_statuses = {
        "NEW",
        "IN_PROGRESS",
        "READY_FOR_PAYMENT",
        "PAID",
        "new",
        "in_progress",
        "ready_for_payment",
        "paid",
    }

    requests = get_all_requests()

    for request in requests:
        if getattr(request, "user_id", None) == user_id:
            status = str(getattr(request, "status", "") or "")
            if status in active_statuses:
                return request

    return None

def get_all_requests(status: Optional[str] = None) -> list[Request]:
    conn = get_conn()

    if status:
        rows = conn.execute(
            "SELECT * FROM requests WHERE status = ? ORDER BY id DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()

    conn.close()
    return [_request_from_row(row) for row in rows]


def update_request(request_id: int, **kwargs):
    conn = get_conn()
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [request_id]
    conn.execute(f"UPDATE requests SET {sets} WHERE id = ?", vals)
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────
#  CRUD: Результаты поиска
# ──────────────────────────────────────────────

def create_search_result(
    request_id: int, title: str = "", price: Optional[int] = None,
    source: str = "", url: str = "", snippet: str = "", score: float = 0.0,
    risk_flags: str = "[]", status: str = "CANDIDATE", admin_note: str = "",
    origin: str = "auto", sort_order: int = 0,
    price_verified: bool = False, link_check_status: str = LinkCheckStatus.NEEDED.value,
    facts_json: str = "",
) -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    link_check_status = normalize_link_check_status(link_check_status)
    cur = conn.execute(
        """INSERT INTO search_results
           (request_id, title, price, source, url, snippet, score, risk_flags,
            status, admin_note, origin, sort_order, price_verified,
            link_check_status, facts_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (request_id, title, price, source, url, snippet, score, risk_flags,
         status, admin_note, origin, sort_order, int(price_verified),
         link_check_status, facts_json, now, now),
    )
    conn.commit()
    result_id = cur.lastrowid
    conn.close()
    return result_id


def get_search_results(request_id: int) -> list[SearchResult]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM search_results WHERE request_id = ? AND origin != 'alice'
           ORDER BY sort_order ASC, id ASC""",
        (request_id,),
    ).fetchall()
    conn.close()
    return [_search_result_from_row(row) for row in rows]


def get_all_search_results(request_id: int) -> list[SearchResult]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM search_results WHERE request_id = ? ORDER BY sort_order ASC, id ASC",
        (request_id,),
    ).fetchall()
    conn.close()
    return [_search_result_from_row(row) for row in rows]


def get_alice_results(request_id: int) -> list[SearchResult]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM search_results WHERE request_id = ? AND origin = 'alice'
           ORDER BY sort_order ASC, id ASC""",
        (request_id,),
    ).fetchall()
    conn.close()
    return [_search_result_from_row(row) for row in rows]


def get_alice_top_result(request_id: int) -> Optional[SearchResult]:
    conn = get_conn()
    row = conn.execute(
        """SELECT * FROM search_results
           WHERE request_id = ? AND origin = 'alice' AND status = 'BEST'
           ORDER BY sort_order ASC, id ASC LIMIT 1""",
        (request_id,),
    ).fetchone()
    conn.close()
    return _search_result_from_row(row) if row else None


def set_alice_top_result(request_id: int, result_id: int) -> Optional[SearchResult]:
    conn = get_conn()
    conn.execute(
        "UPDATE search_results SET status = 'APPROVED', updated_at = ? WHERE request_id = ? AND origin = 'alice' AND status = 'BEST'",
        (datetime.now().isoformat(), request_id),
    )
    conn.execute(
        "UPDATE search_results SET status = 'BEST', updated_at = ? WHERE id = ?",
        (datetime.now().isoformat(), result_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM search_results WHERE id = ?", (result_id,)).fetchone()
    conn.close()
    return _search_result_from_row(row) if row else None


def replace_alice_results(request_id: int, items: list[dict]):
    """Заменяет все alice-карточки для заявки новыми.

    Принимает items в формате парсера Алисы (name, price_num, store, link,
    pluses, risks) и корректно переносит их в поля таблицы search_results.
    """
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "DELETE FROM search_results WHERE request_id = ? AND origin = 'alice'",
        (request_id,),
    )
    for idx, item in enumerate(items, 1):
        # ── маппинг полей парсера → поля БД ──
        title = (item.get("name") or item.get("title") or "").strip()
        price = (
            item.get("price_num")
            if item.get("price_num") is not None
            else item.get("price")
        )
        price = to_int_price(price)
        source = (item.get("store") or item.get("source") or "магазин нужно уточнить").strip()
        raw_url = (item.get("link") or item.get("url") or "").strip()

        # Плюсы → snippet
        pluses = item.get("pluses") or []
        if isinstance(pluses, str):
            pluses = [pluses]
        snippet = (
            item.get("snippet")
            or item.get("why")
            or ("; ".join(pluses) if pluses else "")
        )

        # Риски → risk_flags
        risks = item.get("risks") or []
        if isinstance(risks, str):
            risks = [risks]
        risk_flags = item.get("risk_flags") or risks
        if not isinstance(risk_flags, list):
            risk_flags = [str(risk_flags)]

        role = (item.get("role") or "").strip().upper()
        status = {
            "BEST": "BEST",
            "BACKUP": "BACKUP",
            "BUDGET": "BUDGET",
            "CAUTION": "DO_NOT_BUY",
            "REJECTED": "REJECTED",
        }.get(role, "APPROVED")

        manual_check = item.get("manual_check") or item.get("notes") or []
        if isinstance(manual_check, str):
            manual_check = [manual_check] if manual_check.strip() else []
        confidence = (item.get("confidence") or "").strip()
        admin_meta = {
            "role": role,
            "confidence": confidence,
            "manual_check": manual_check,
        }
        admin_note = item.get("admin_note") or json.dumps(admin_meta, ensure_ascii=False)

        # Определяем статус ссылки
        is_empty_link = (
            not raw_url
            or raw_url in ("ссылку нужно искать вручную", ".", "-", "нет", "—")
        )
        url = "" if is_empty_link else raw_url
        link_check_status = (
            LinkCheckStatus.FOUND_UNVERIFIED.value
            if (url and url.startswith("http"))
            else LinkCheckStatus.NEEDED.value
        )
        link_check_status = normalize_link_check_status(link_check_status)

        conn.execute(
            """INSERT INTO search_results
               (request_id, title, price, source, url, snippet, score, risk_flags,
                status, admin_note, origin, sort_order, price_verified,
                link_check_status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'alice', ?, 0, ?, ?, ?)""",
            (request_id,
             title,
             price,
             source,
             url,
             snippet,
             item.get("score", 0.0),
             json.dumps(risk_flags, ensure_ascii=False),
             status,
             admin_note,
             idx,
             link_check_status,
             now, now),
        )
    conn.commit()
    conn.close()


def move_alice_result(result_id: int, direction: int) -> Optional[SearchResult]:
    """Меняет порядок карточки Алисы: direction=-1 выше, direction=1 ниже."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM search_results WHERE id = ? AND origin = 'alice'",
        (result_id,),
    ).fetchone()
    if not row:
        conn.close()
        return None
    rows = conn.execute(
        """SELECT id FROM search_results WHERE request_id = ? AND origin = 'alice'
           ORDER BY sort_order ASC, id ASC""",
        (row["request_id"],),
    ).fetchall()
    ids = [int(item["id"]) for item in rows]
    index = ids.index(result_id)
    target_index = index + direction
    if 0 <= target_index < len(ids):
        ids[index], ids[target_index] = ids[target_index], ids[index]
        now = datetime.now().isoformat()
        conn.executemany(
            "UPDATE search_results SET sort_order = ?, updated_at = ? WHERE id = ?",
            [(position, now, item_id) for position, item_id in enumerate(ids, 1)],
        )
        conn.commit()
    updated = conn.execute("SELECT * FROM search_results WHERE id = ?", (result_id,)).fetchone()
    conn.close()
    return SearchResult(**dict(updated))


def get_search_result(result_id: int) -> Optional[SearchResult]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM search_results WHERE id = ?", (result_id,)).fetchone()
    conn.close()
    return _search_result_from_row(row) if row else None


def update_search_result(result_id: int, **kwargs):
    conn = get_conn()
    if "link_check_status" in kwargs:
        kwargs["link_check_status"] = normalize_link_check_status(kwargs["link_check_status"])
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [result_id]
    conn.execute(f"UPDATE search_results SET {sets} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def delete_search_result(result_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM search_results WHERE id = ?", (result_id,))
    conn.commit()
    conn.close()


def count_search_results(request_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM search_results WHERE request_id = ?",
        (request_id,)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def count_approved_results(request_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM search_results 
           WHERE request_id = ? AND status IN ('APPROVED', 'BEST', 'CHEAP', 'RELIABLE')""",
        (request_id,)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


# ──────────────────────────────────────────────
#  CRUD: Market Checks (проверка рынка)
# ──────────────────────────────────────────────

def create_market_check(
    request_id: int, title: str = "", price: Optional[int] = None,
    source: str = "", url: str = "", rating_reviews: str = "",
    city: str = "", condition: str = "", verdict: str = "",
    reason: str = "",
) -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    cur = conn.execute(
        """INSERT INTO market_checks
           (request_id, title, price, source, url, rating_reviews, city,
            condition, verdict, reason, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (request_id, title, price, source, url, rating_reviews, city,
         condition, verdict, reason, now, now),
    )
    conn.commit()
    check_id = cur.lastrowid
    conn.close()
    return check_id


def get_market_checks(request_id: int) -> list[MarketCheck]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM market_checks WHERE request_id = ? ORDER BY id ASC",
        (request_id,),
    ).fetchall()
    conn.close()
    return [MarketCheck(**dict(row)) for row in rows]


def get_market_check(check_id: int) -> Optional[MarketCheck]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM market_checks WHERE id = ?", (check_id,)).fetchone()
    conn.close()
    return MarketCheck(**dict(row)) if row else None


def update_market_check(check_id: int, **kwargs):
    conn = get_conn()
    kwargs["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [check_id]
    conn.execute(f"UPDATE market_checks SET {sets} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def delete_market_check(check_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM market_checks WHERE id = ?", (check_id,))
    conn.commit()
    conn.close()


def has_market_check(request_id: int) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM market_checks WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    conn.close()
    return (row["cnt"] if row else 0) > 0


# ──────────────────────────────────────────────
#  Диагностика автопоиска
# ──────────────────────────────────────────────

def create_search_attempt(
    request_id: int,
    source: str,
    query: str,
    status: str,
    found_count: int = 0,
    kept_count: int = 0,
    error_text: str = "",
) -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    cur = conn.execute(
        """INSERT INTO search_attempts
           (request_id, source, query, status, found_count, kept_count, error_text, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (request_id, source, query, status, found_count, kept_count, error_text[:1000], now),
    )
    conn.commit()
    attempt_id = cur.lastrowid
    conn.close()
    return attempt_id


def get_search_attempts(request_id: int, limit: int = 60) -> list[SearchAttempt]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM search_attempts WHERE request_id = ?
           ORDER BY id DESC LIMIT ?""",
        (request_id, limit),
    ).fetchall()
    conn.close()
    return [SearchAttempt(**dict(row)) for row in rows]


def replace_manual_search_links(request_id: int, links: list[dict]) -> None:
    """Сохраняет fallback-ссылки отдельно от товарных кандидатов."""
    conn = get_conn()
    conn.execute("DELETE FROM manual_search_links WHERE request_id = ?", (request_id,))
    now = datetime.now().isoformat()
    conn.executemany(
        """INSERT INTO manual_search_links (request_id, source, query, url, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        [
            (request_id, link.get("site", ""), link.get("query", ""), link.get("url", ""), now)
            for link in links
            if link.get("url")
        ],
    )
    conn.commit()
    conn.close()


def get_manual_search_links(request_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT source, query, url FROM manual_search_links
           WHERE request_id = ? ORDER BY id""",
        (request_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
