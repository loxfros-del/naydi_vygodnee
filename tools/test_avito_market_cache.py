"""Reference cache persistence and final-refresh freshness regressions, offline."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from avito_service.market_cache import MarketSnapshotCache
from avito_service.models import AnalyzedListing, CollectionBatch, SearchRequest
from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig
from avito_service.normalization import normalize_listing
from avito_service.risk_rules import evaluate_rules
from tools.test_avito_collection_quality import EvidenceProvider, EvidenceReviewer, request, row, service


def evidence():
    listing = normalize_listing(row(12345678, 40_000))
    return AnalyzedListing(listing, evaluate_rules(listing), EvidenceReviewer().review_text(listing, request()))


class AvitoMarketCacheTests(unittest.TestCase):
    def test_ui_category_aliases_preserve_category_filter(self):
        provider = ZenStudioProvider(ServiceConfig(apify_token="offline"))
        for ui_category, actor_category in (("household", "appliances"), ("auto", "spare_parts")):
            with self.subTest(category=ui_category):
                payload = provider._actor_input(replace(request(), category=ui_category))
                self.assertEqual(payload["category"], actor_category)

    def test_conflicting_reference_prices_cannot_promote_photo_candidate(self):
        search = replace(request(), required_storage=None)
        first = normalize_listing(row(10000000, 28_000))
        second = replace(normalize_listing(row(10000001, 29_000)),
                         title="iPhone 13, 256 ГБ",
                         parameters={**first.parameters, "Встроенная память": "256 ГБ"})
        reviewer = EvidenceReviewer()
        listings = (first, second)
        reviews = {item.listing_id: reviewer.review_text(item, search) for item in listings}
        risks = {item.listing_id: evaluate_rules(item) for item in listings}
        references = []
        for index in range(3):
            listing = replace(normalize_listing(row(20000000 + index, 80_000)),
                              title="iPhone 13, 256 ГБ",
                              parameters={**first.parameters, "Встроенная память": "256 ГБ"})
            review = replace(reviewer.review_text(listing, search), conflicts=("Разные версии товара",))
            references.append(AnalyzedListing(listing, evaluate_rules(listing), review))
        engine = service(EvidenceProvider())
        baseline = engine._photo_candidate_ids(listings, risks, reviews, 1, search)
        with_conflicts = engine._photo_candidate_ids(listings, risks, reviews, 1, search, tuple(references))
        self.assertEqual(baseline, (first.listing_id,))
        self.assertEqual(with_conflicts, baseline)

    def test_disk_snapshot_reused_across_restart_without_raw_identity(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            cache = MarketSnapshotCache(path)
            key = cache.key(request())
            cache.put(key, (evidence(),))
            loaded = MarketSnapshotCache(path).get(key)
            self.assertEqual(loaded[0].listing.seller.identity_hash, evidence().listing.seller.identity_hash)
            self.assertEqual(loaded[0].listing.verification_status, "unverified")
            raw = (path / f"{key}.json").read_text(encoding="utf-8")
            self.assertNotIn('"userKey"', raw)
            self.assertNotIn('"phone"', raw)

    def test_price_bounds_reuse_market_but_model_and_region_do_not(self):
        key = MarketSnapshotCache.key(request())
        self.assertEqual(key, MarketSnapshotCache.key(replace(request(), price_max=50_000)))
        self.assertNotEqual(key, MarketSnapshotCache.key(replace(request(), query="iPhone 14")))
        self.assertNotEqual(key, MarketSnapshotCache.key(replace(request(), location="Казань")))

    def test_market_identity_normalizes_ps5_aliases_but_not_category(self):
        first = SearchRequest("PS5", location="Ярославль", category="gaming", price_max=50_000)
        alias = replace(first, query="PlayStation 5", price_max=45_000)
        incompatible = replace(first, category="electronics")
        self.assertEqual(MarketSnapshotCache.key(first), MarketSnapshotCache.key(alias))
        self.assertNotEqual(MarketSnapshotCache.key(first), MarketSnapshotCache.key(incompatible))

    def test_expired_source_evidence_never_becomes_fresh_on_cache_write(self):
        cache = MarketSnapshotCache()
        old = evidence()
        old = replace(old, listing=replace(old.listing,
            collected_at=(datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()))
        key = cache.key(request())
        cache.put(key, (old,))
        self.assertIsNone(cache.get(key))

    def test_corrupt_disk_snapshot_is_a_miss_and_cache_is_bounded(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            cache = MarketSnapshotCache(path, max_entries=2)
            key = cache.key(request())
            (path / f"{key}.json").write_text("{broken", encoding="utf-8")
            self.assertIsNone(cache.get(key))
            for model in ("iPhone 12", "iPhone 13", "iPhone 14"):
                cache.put(cache.key(replace(request(), query=model)), (evidence(),))
            self.assertLessEqual(len(list(path.glob("*.json"))), 2)

    def test_refreshed_old_actor_cache_is_not_relabelled_as_checked_now(self):
        class CachedRefresh(EvidenceProvider):
            def refresh(self, listings, **kwargs):
                batch = super().refresh(listings, **kwargs)
                stamp = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
                return replace(batch, items=tuple(dict(item, scrapedAt=stamp) for item in batch.items))
        report = service(CachedRefresh()).search(request())
        self.assertEqual(report.public_dict()["recommendations"], [])
        self.assertTrue(all(not item.analyzed.listing.verified_at for item in report.recommendations))


if __name__ == "__main__":
    unittest.main()
