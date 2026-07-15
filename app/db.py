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


def _json_text(value, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)

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
    category: str = ""
    request_mode: str = ""
    requirements_json: str = "{}"
    priority: str = ""
    condition: str = ""
    package_code: str = ""
    progress_message_id: Optional[int] = None
    admin_note: str = ""


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
    ai_card_status: str = ""
    image_file_id: str = ""
    checked_at: str = ""


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


@dataclass
class RequestTransition:
    id: int
    request_id: int
    from_status: str = ""
    to_status: str = ""
    actor: str = ""
    reason: str = ""
    extra_fields_json: str = "{}"
    created_at: str = ""


@dataclass
class ProductEvent:
    id: int
    event_type: str
    user_id: Optional[int] = None
    request_id: Optional[int] = None
    payload_json: str = "{}"
    created_at: str = ""


@dataclass
class Feedback:
    id: int
    user_id: int
    request_id: Optional[int] = None
    rating: int = 0
    feedback_type: str = "rating"
    comment: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SavedProduct:
    id: int
    user_id: int
    request_id: Optional[int] = None
    search_result_id: Optional[int] = None
    title: str = ""
    price: Optional[int] = None
    source: str = ""
    url: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ComparisonLink:
    id: int
    user_id: int
    request_id: Optional[int] = None
    url: str = ""
    normalized_url: str = ""
    source: str = ""
    title: str = ""
    model: str = ""
    price: Optional[int] = None
    availability: str = ""
    seller: str = ""
    facts_json: str = "{}"
    risk_flags: str = "[]"
    status: str = "PENDING"
    is_blocked: bool = False
    manual_check_required: bool = False
    manual_note: str = ""
    sort_order: int = 0
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
    # Только additive-миграции: старые строки и статусы не переписываются.
    request_columns = {
        "username": "TEXT DEFAULT ''",
        "product": "TEXT DEFAULT ''",
        "product_name": "TEXT DEFAULT ''",
        "use_case": "TEXT DEFAULT ''",
        "budget": "TEXT DEFAULT ''",
        "city": "TEXT DEFAULT ''",
        "important_criteria": "TEXT DEFAULT ''",
        "clean_search_query": "TEXT DEFAULT ''",
        "is_used_allowed": "INTEGER DEFAULT 0",
        "original_query": "TEXT DEFAULT ''",
        "status": "TEXT DEFAULT 'NEW'",
        "found_products": "TEXT DEFAULT '[]'",
        "preview_text": "TEXT DEFAULT ''",
        "report_text": "TEXT DEFAULT ''",
        "alice_response": "TEXT DEFAULT ''",
        "search_links": "TEXT DEFAULT '[]'",
        "created_at": "TEXT DEFAULT ''",
        "updated_at": "TEXT DEFAULT ''",
        "category": "TEXT DEFAULT ''",
        "request_mode": "TEXT DEFAULT ''",
        "requirements_json": "TEXT DEFAULT '{}'",
        "priority": "TEXT DEFAULT ''",
        "condition": "TEXT DEFAULT ''",
        "package_code": "TEXT DEFAULT ''",
        "progress_message_id": "INTEGER",
        "admin_note": "TEXT DEFAULT ''",
    }
    for column, definition in request_columns.items():
        _ensure_column(conn, "requests", column, definition)

    result_columns = {
        "title": "TEXT DEFAULT ''",
        "price": "INTEGER",
        "source": "TEXT DEFAULT ''",
        "url": "TEXT DEFAULT ''",
        "snippet": "TEXT DEFAULT ''",
        "score": "REAL DEFAULT 0.0",
        "risk_flags": "TEXT DEFAULT '[]'",
        "status": "TEXT DEFAULT 'CANDIDATE'",
        "admin_note": "TEXT DEFAULT ''",
        "origin": "TEXT DEFAULT 'auto'",
        "sort_order": "INTEGER DEFAULT 0",
        "price_verified": "INTEGER DEFAULT 0",
        "link_check_status": "TEXT DEFAULT 'NEEDED'",
        "facts_json": "TEXT DEFAULT ''",
        "created_at": "TEXT DEFAULT ''",
        "updated_at": "TEXT DEFAULT ''",
        "ai_card_status": "TEXT DEFAULT ''",
        "image_file_id": "TEXT DEFAULT ''",
        "checked_at": "TEXT DEFAULT ''",
    }
    for column, definition in result_columns.items():
        _ensure_column(conn, "search_results", column, definition)


def _create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_requests_user_status
            ON requests(user_id, status, id DESC);
        CREATE INDEX IF NOT EXISTS idx_requests_status_created
            ON requests(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_requests_category_created
            ON requests(category, created_at);
        CREATE INDEX IF NOT EXISTS idx_search_results_request_origin_status
            ON search_results(request_id, origin, status);
        CREATE INDEX IF NOT EXISTS idx_request_transitions_request_created
            ON request_transitions(request_id, id);
        CREATE INDEX IF NOT EXISTS idx_product_events_type_created
            ON product_events(event_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_product_events_request_created
            ON product_events(request_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_feedback_request_created
            ON feedback(request_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_feedback_rating_created
            ON feedback(rating, created_at);
        CREATE INDEX IF NOT EXISTS idx_saved_products_user_created
            ON saved_products(user_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_comparison_links_request_order
            ON comparison_links(request_id, sort_order, id);
        CREATE INDEX IF NOT EXISTS idx_comparison_links_user_created
            ON comparison_links(user_id, id DESC);
        """
    )


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
            category TEXT DEFAULT '',
            request_mode TEXT DEFAULT '',
            requirements_json TEXT DEFAULT '{}',
            priority TEXT DEFAULT '',
            condition TEXT DEFAULT '',
            package_code TEXT DEFAULT '',
            progress_message_id INTEGER,
            admin_note TEXT DEFAULT '',
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
            ai_card_status TEXT DEFAULT '',
            image_file_id TEXT DEFAULT '',
            checked_at TEXT DEFAULT '',
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

        CREATE TABLE IF NOT EXISTS request_transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            from_status TEXT DEFAULT '',
            to_status TEXT DEFAULT '',
            actor TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            extra_fields_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT '',
            FOREIGN KEY (request_id) REFERENCES requests(id)
        );

        CREATE TABLE IF NOT EXISTS product_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            user_id INTEGER,
            request_id INTEGER,
            payload_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT '',
            FOREIGN KEY (request_id) REFERENCES requests(id)
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            request_id INTEGER,
            rating INTEGER DEFAULT 0,
            feedback_type TEXT DEFAULT 'rating',
            comment TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            FOREIGN KEY (request_id) REFERENCES requests(id)
        );

        CREATE TABLE IF NOT EXISTS saved_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            request_id INTEGER,
            search_result_id INTEGER,
            title TEXT DEFAULT '',
            price INTEGER,
            source TEXT DEFAULT '',
            url TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            FOREIGN KEY (request_id) REFERENCES requests(id),
            FOREIGN KEY (search_result_id) REFERENCES search_results(id)
        );

        CREATE TABLE IF NOT EXISTS comparison_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            request_id INTEGER,
            url TEXT DEFAULT '',
            normalized_url TEXT DEFAULT '',
            source TEXT DEFAULT '',
            title TEXT DEFAULT '',
            model TEXT DEFAULT '',
            price INTEGER,
            availability TEXT DEFAULT '',
            seller TEXT DEFAULT '',
            facts_json TEXT DEFAULT '{}',
            risk_flags TEXT DEFAULT '[]',
            status TEXT DEFAULT 'PENDING',
            is_blocked INTEGER DEFAULT 0,
            manual_check_required INTEGER DEFAULT 0,
            manual_note TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            FOREIGN KEY (request_id) REFERENCES requests(id)
        );
    """)
    _run_migrations(conn)
    _create_indexes(conn)
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────
#  CRUD: Заявки
# ──────────────────────────────────────────────

def create_request(user_id: int, username: str = "", product: str = "",
                   product_name: str = "", use_case: str = "", budget: str = "",
                   city: str = "", important_criteria: str = "",
                   clean_search_query: str = "", is_used_allowed: bool = False,
                   original_query: str = "", category: str = "",
                   request_mode: str = "", requirements_json: str = "{}",
                   priority: str = "", condition: str = "",
                   package_code: str = "", progress_message_id: Optional[int] = None,
                   admin_note: str = "") -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    cur = conn.execute(
        """INSERT INTO requests
           (user_id, username, product, product_name, use_case, budget, city,
            important_criteria, clean_search_query, is_used_allowed,
            original_query, status, category, request_mode, requirements_json,
            priority, condition, package_code, progress_message_id, admin_note,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, username, product, product_name, use_case, budget, city,
         important_criteria, clean_search_query, int(is_used_allowed),
         original_query, category, request_mode, requirements_json, priority,
         condition, package_code, progress_message_id, admin_note, now, now),
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
        "NEED_CLARIFICATION",
        "WAITING_PAYMENT",
        "SEARCHING",
        "ADMIN_REVIEW",
        "AI_CARDS_DRAFT",
        "READY",
        # Старые статусы читаются до их естественного перехода.
        "QUESTIONS",
        "IN_PROGRESS",
        "HUMAN_REVIEW",
        "PREVIEW_SENT",
        "READY_FOR_PAYMENT",
        "PAID",
    }
    for request in get_user_requests(user_id):
        status = str(getattr(request, "status", "") or "").strip().upper()
        if status in active_statuses:
            return request

    return None


def get_user_requests(
    user_id: int,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[Request]:
    """Возвращает заявки пользователя от новых к старым."""
    conn = get_conn()
    safe_limit = max(1, min(int(limit), 1_000))
    if status:
        rows = conn.execute(
            """SELECT * FROM requests
               WHERE user_id = ? AND UPPER(status) = ?
               ORDER BY id DESC LIMIT ?""",
            (user_id, str(status).strip().upper(), safe_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM requests WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, safe_limit),
        ).fetchall()
    conn.close()
    return [_request_from_row(row) for row in rows]

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
    facts_json: str = "", ai_card_status: str = "", image_file_id: str = "",
    checked_at: str = "",
) -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    link_check_status = normalize_link_check_status(link_check_status)
    # Низкоуровневые ручные/тестовые Alice-карточки исторически считались
    # утверждёнными. Генераторы используют replace_alice_results и всегда
    # передают явный DRAFT/GENERATED lifecycle.
    if str(origin or "").lower() == "alice" and not ai_card_status:
        ai_card_status = "APPROVED"
    cur = conn.execute(
        """INSERT INTO search_results
           (request_id, title, price, source, url, snippet, score, risk_flags,
            status, admin_note, origin, sort_order, price_verified,
            link_check_status, facts_json, ai_card_status, image_file_id,
            checked_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (request_id, title, price, source, url, snippet, score, risk_flags,
         status, admin_note, origin, sort_order, int(price_verified),
         link_check_status, facts_json, ai_card_status, image_file_id,
         checked_at, now, now),
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
            "candidate_id": item.get("candidate_id"),
        }
        admin_note = item.get("admin_note") or json.dumps(admin_meta, ensure_ascii=False)
        facts_json = _json_text(item.get("facts_json") or item.get("facts"), "{}")

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
                link_check_status, facts_json, ai_card_status, image_file_id, checked_at,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'alice', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
             int(bool(item.get("price_verified"))),
             link_check_status,
             facts_json,
             str(item.get("ai_card_status") or "DRAFT").strip().upper(),
             str(item.get("image_file_id") or "").strip()[:512],
             str(item.get("checked_at") or "").strip(),
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


# ──────────────────────────────────────────────
#  Журнал переходов заявки
# ──────────────────────────────────────────────

def record_request_transition(
    request_id: int,
    from_status: str,
    to_status: str,
    *,
    actor: str = "",
    reason: str = "",
    extra_fields=None,
    created_at: Optional[str] = None,
) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO request_transitions
           (request_id, from_status, to_status, actor, reason, extra_fields_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            request_id,
            from_status,
            to_status,
            actor,
            reason,
            _json_text(extra_fields, "{}"),
            created_at or datetime.now().isoformat(),
        ),
    )
    conn.commit()
    transition_id = cur.lastrowid
    conn.close()
    return transition_id


def get_request_transitions(request_id: int, limit: int = 200) -> list[RequestTransition]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM request_transitions WHERE request_id = ?
           ORDER BY id DESC LIMIT ?""",
        (request_id, max(1, min(int(limit), 2_000))),
    ).fetchall()
    conn.close()
    return [RequestTransition(**dict(row)) for row in rows]


# ──────────────────────────────────────────────
#  Продуктовые события
# ──────────────────────────────────────────────

def create_product_event(
    event_type: str,
    *,
    user_id: Optional[int] = None,
    request_id: Optional[int] = None,
    payload=None,
    created_at: Optional[str] = None,
) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO product_events
           (event_type, user_id, request_id, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            str(event_type or "").strip(),
            user_id,
            request_id,
            _json_text(payload, "{}"),
            created_at or datetime.now().isoformat(),
        ),
    )
    conn.commit()
    event_id = cur.lastrowid
    conn.close()
    return event_id


def get_product_event(event_id: int) -> Optional[ProductEvent]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM product_events WHERE id = ?", (event_id,)).fetchone()
    conn.close()
    return ProductEvent(**dict(row)) if row else None


def get_product_events(
    *,
    event_type: Optional[str] = None,
    user_id: Optional[int] = None,
    request_id: Optional[int] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    limit: int = 1_000,
) -> list[ProductEvent]:
    clauses: list[str] = []
    values: list[object] = []
    if event_type:
        clauses.append("event_type = ?")
        values.append(str(event_type).strip())
    if user_id is not None:
        clauses.append("user_id = ?")
        values.append(user_id)
    if request_id is not None:
        clauses.append("request_id = ?")
        values.append(request_id)
    if created_from:
        clauses.append("created_at >= ?")
        values.append(created_from)
    if created_to:
        clauses.append("created_at <= ?")
        values.append(created_to)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    values.append(max(1, min(int(limit), 10_000)))
    conn = get_conn()
    rows = conn.execute(
        f"SELECT * FROM product_events {where} ORDER BY id ASC LIMIT ?",
        values,
    ).fetchall()
    conn.close()
    return [ProductEvent(**dict(row)) for row in rows]


# ──────────────────────────────────────────────
#  Обратная связь
# ──────────────────────────────────────────────

def create_feedback(
    *,
    user_id: int,
    request_id: Optional[int] = None,
    rating: int,
    feedback_type: str = "rating",
    comment: str = "",
) -> int:
    rating_value = int(rating)
    if not 1 <= rating_value <= 5:
        raise ValueError("rating должен быть от 1 до 5")
    conn = get_conn()
    now = datetime.now().isoformat()
    cur = conn.execute(
        """INSERT INTO feedback
           (user_id, request_id, rating, feedback_type, comment, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, request_id, rating_value, feedback_type, comment, now, now),
    )
    conn.commit()
    feedback_id = cur.lastrowid
    conn.close()
    return feedback_id


def get_feedback(feedback_id: int) -> Optional[Feedback]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
    conn.close()
    return Feedback(**dict(row)) if row else None


def get_request_feedback(request_id: int) -> list[Feedback]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM feedback WHERE request_id = ? ORDER BY id DESC",
        (request_id,),
    ).fetchall()
    conn.close()
    return [Feedback(**dict(row)) for row in rows]


def get_low_feedback(max_rating: int = 2, limit: int = 100) -> list[Feedback]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM feedback WHERE rating <= ?
           ORDER BY id DESC LIMIT ?""",
        (max(1, min(int(max_rating), 5)), max(1, min(int(limit), 1_000))),
    ).fetchall()
    conn.close()
    return [Feedback(**dict(row)) for row in rows]


def update_feedback(feedback_id: int, *, rating: Optional[int] = None,
                    feedback_type: Optional[str] = None,
                    comment: Optional[str] = None) -> Optional[Feedback]:
    updates: dict[str, object] = {}
    if rating is not None:
        rating_value = int(rating)
        if not 1 <= rating_value <= 5:
            raise ValueError("rating должен быть от 1 до 5")
        updates["rating"] = rating_value
    if feedback_type is not None:
        updates["feedback_type"] = feedback_type
    if comment is not None:
        updates["comment"] = comment
    if not updates:
        return get_feedback(feedback_id)
    updates["updated_at"] = datetime.now().isoformat()
    conn = get_conn()
    sets = ", ".join(f"{column} = ?" for column in updates)
    conn.execute(
        f"UPDATE feedback SET {sets} WHERE id = ?",
        [*updates.values(), feedback_id],
    )
    conn.commit()
    conn.close()
    return get_feedback(feedback_id)


# ──────────────────────────────────────────────
#  Сохранённые товары
# ──────────────────────────────────────────────

def _saved_product_from_row(row) -> Optional[SavedProduct]:
    if not row:
        return None
    data = dict(row)
    data["price"] = to_int_price(data.get("price"))
    return SavedProduct(**data)


def save_product(
    *,
    user_id: int,
    request_id: Optional[int] = None,
    search_result_id: Optional[int] = None,
    title: str = "",
    price: Optional[int] = None,
    source: str = "",
    url: str = "",
    note: str = "",
) -> int:
    conn = get_conn()
    existing = None
    if search_result_id is not None:
        existing = conn.execute(
            "SELECT id FROM saved_products WHERE user_id = ? AND search_result_id = ? LIMIT 1",
            (user_id, search_result_id),
        ).fetchone()
    elif url:
        existing = conn.execute(
            "SELECT id FROM saved_products WHERE user_id = ? AND url = ? LIMIT 1",
            (user_id, url),
        ).fetchone()
    now = datetime.now().isoformat()
    if existing:
        saved_id = int(existing["id"])
        conn.execute(
            """UPDATE saved_products SET request_id = ?, title = ?, price = ?,
               source = ?, url = ?, note = ?, updated_at = ? WHERE id = ?""",
            (request_id, title, to_int_price(price), source, url, note, now, saved_id),
        )
    else:
        cur = conn.execute(
            """INSERT INTO saved_products
               (user_id, request_id, search_result_id, title, price, source, url,
                note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, request_id, search_result_id, title, to_int_price(price),
             source, url, note, now, now),
        )
        saved_id = cur.lastrowid
    conn.commit()
    conn.close()
    return saved_id


def get_saved_product(saved_id: int) -> Optional[SavedProduct]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM saved_products WHERE id = ?", (saved_id,)).fetchone()
    conn.close()
    return _saved_product_from_row(row)


def get_saved_products(user_id: int, limit: int = 100) -> list[SavedProduct]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM saved_products WHERE user_id = ?
           ORDER BY id DESC LIMIT ?""",
        (user_id, max(1, min(int(limit), 1_000))),
    ).fetchall()
    conn.close()
    return [_saved_product_from_row(row) for row in rows]


def delete_saved_product(saved_id: int, *, user_id: Optional[int] = None) -> bool:
    conn = get_conn()
    if user_id is None:
        cur = conn.execute("DELETE FROM saved_products WHERE id = ?", (saved_id,))
    else:
        cur = conn.execute(
            "DELETE FROM saved_products WHERE id = ? AND user_id = ?",
            (saved_id, user_id),
        )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# ──────────────────────────────────────────────
#  Ссылки пользовательского сравнения
# ──────────────────────────────────────────────

def _comparison_link_from_row(row) -> Optional[ComparisonLink]:
    if not row:
        return None
    data = dict(row)
    data["price"] = to_int_price(data.get("price"))
    data["is_blocked"] = bool(data.get("is_blocked"))
    data["manual_check_required"] = bool(data.get("manual_check_required"))
    return ComparisonLink(**data)


def create_comparison_link(
    *,
    user_id: int,
    request_id: Optional[int],
    url: str,
    normalized_url: str = "",
    source: str = "",
    title: str = "",
    model: str = "",
    price: Optional[int] = None,
    availability: str = "",
    seller: str = "",
    facts_json="{}",
    risk_flags="[]",
    status: str = "PENDING",
    is_blocked: bool = False,
    manual_check_required: bool = False,
    manual_note: str = "",
    sort_order: int = 0,
) -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    cur = conn.execute(
        """INSERT INTO comparison_links
           (user_id, request_id, url, normalized_url, source, title, model, price,
            availability, seller, facts_json, risk_flags, status, is_blocked,
            manual_check_required, manual_note, sort_order, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id, request_id, url, normalized_url or url, source, title, model,
            to_int_price(price), availability, seller, _json_text(facts_json, "{}"),
            _json_text(risk_flags, "[]"), status, int(is_blocked),
            int(manual_check_required), manual_note, int(sort_order), now, now,
        ),
    )
    conn.commit()
    link_id = cur.lastrowid
    conn.close()
    return link_id


def get_comparison_link(link_id: int) -> Optional[ComparisonLink]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM comparison_links WHERE id = ?", (link_id,)).fetchone()
    conn.close()
    return _comparison_link_from_row(row)


def get_comparison_links(request_id: int) -> list[ComparisonLink]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM comparison_links WHERE request_id = ?
           ORDER BY sort_order ASC, id ASC""",
        (request_id,),
    ).fetchall()
    conn.close()
    return [_comparison_link_from_row(row) for row in rows]


def replace_comparison_links(
    *,
    user_id: int,
    request_id: int,
    items: list[dict],
) -> list[ComparisonLink]:
    conn = get_conn()
    now = datetime.now().isoformat()
    try:
        conn.execute("BEGIN")
        conn.execute(
            "DELETE FROM comparison_links WHERE request_id = ? AND user_id = ?",
            (request_id, user_id),
        )
        for index, item in enumerate(items, 1):
            conn.execute(
                """INSERT INTO comparison_links
                   (user_id, request_id, url, normalized_url, source, title, model,
                    price, availability, seller, facts_json, risk_flags, status,
                    is_blocked, manual_check_required, manual_note, sort_order,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    request_id,
                    item.get("url", ""),
                    item.get("normalized_url") or item.get("url", ""),
                    item.get("source", ""),
                    item.get("title", ""),
                    item.get("model", ""),
                    to_int_price(item.get("price")),
                    item.get("availability", ""),
                    item.get("seller", ""),
                    _json_text(item.get("facts_json", item.get("facts")), "{}"),
                    _json_text(item.get("risk_flags", item.get("risks")), "[]"),
                    item.get("status", "PENDING"),
                    int(bool(item.get("is_blocked", False))),
                    int(bool(item.get("manual_check_required", False))),
                    item.get("manual_note", ""),
                    int(item.get("sort_order") or index),
                    now,
                    now,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return get_comparison_links(request_id)


def update_comparison_link(link_id: int, **kwargs) -> Optional[ComparisonLink]:
    allowed = {
        "url", "normalized_url", "source", "title", "model", "price",
        "availability", "seller", "facts_json", "risk_flags", "status",
        "is_blocked", "manual_check_required", "manual_note", "sort_order",
    }
    unknown = set(kwargs) - allowed
    if unknown:
        raise ValueError(f"Неизвестные поля comparison_links: {sorted(unknown)}")
    if not kwargs:
        return get_comparison_link(link_id)
    if "price" in kwargs:
        kwargs["price"] = to_int_price(kwargs["price"])
    for key, default in (("facts_json", "{}"), ("risk_flags", "[]")):
        if key in kwargs:
            kwargs[key] = _json_text(kwargs[key], default)
    for key in ("is_blocked", "manual_check_required"):
        if key in kwargs:
            kwargs[key] = int(bool(kwargs[key]))
    kwargs["updated_at"] = datetime.now().isoformat()
    conn = get_conn()
    sets = ", ".join(f"{column} = ?" for column in kwargs)
    conn.execute(
        f"UPDATE comparison_links SET {sets} WHERE id = ?",
        [*kwargs.values(), link_id],
    )
    conn.commit()
    conn.close()
    return get_comparison_link(link_id)


def delete_comparison_link(link_id: int, *, user_id: Optional[int] = None) -> bool:
    conn = get_conn()
    if user_id is None:
        cur = conn.execute("DELETE FROM comparison_links WHERE id = ?", (link_id,))
    else:
        cur = conn.execute(
            "DELETE FROM comparison_links WHERE id = ? AND user_id = ?",
            (link_id, user_id),
        )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted
