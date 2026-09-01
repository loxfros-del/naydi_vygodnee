"""Проверка готовности отчёта на минимальной валидной заявке."""
from __future__ import annotations

import os
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")

from app import db  # noqa: E402
from app.link_checks import LinkCheckStatus  # noqa: E402
from app.readiness import check_readiness  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db.settings.DB_PATH = str(Path(tmp) / "ready.db")
        db.init_db()

        req_id = db.create_request(
            user_id=1,
            product_name="Телевизор",
            budget="45000",
            city="Ярославль",
        )
        card_id = db.create_search_result(
            request_id=req_id,
            title="Samsung UE43AU7100U",
            price=43000,
            source="DNS",
            url="https://www.dns-shop.ru/product/samsung-ue43au7100u",
            status="BEST",
            origin="alice",
            price_verified=True,
            link_check_status=LinkCheckStatus.VERIFIED.value,
            facts_json=json.dumps({
                "exact_match": "EXACT", "exact_product_verified": True,
                "product_page_verified": True, "price_verified": True,
                "availability_verified": True, "seller_verified": True,
                "available": True,
            }, ensure_ascii=False),
        )
        db.set_alice_top_result(req_id, card_id)
        db.create_market_check(
            request_id=req_id,
            title="Samsung UE43AU7100U",
            price=43000,
            source="DNS",
            url="https://www.dns-shop.ru/product/samsung-ue43au7100u",
            verdict="BUY",
            reason="Цена и ссылка проверены",
        )

        req = db.get_request(req_id)
        result = check_readiness(req)

        assert result.percent >= 75, result
        assert result.can_send is True, result

        # Extra market analysis improves the score but is not a hard blocker
        # once the in-budget TOP-1 is completely verified.
        db.delete_market_check(db.get_market_checks(req_id)[0].id)
        without_market_check = check_readiness(req)
        assert without_market_check.can_send is True, without_market_check

    print("test_report_readiness: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
