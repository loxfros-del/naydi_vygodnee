"""The owner-requested city is encoded in the supported source URL as well."""
import unittest
from urllib.parse import parse_qs, urlparse

from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig
from avito_service.matching import matches_listing_request
from avito_service.models import AIReview, ReviewVerdict, SearchRequest
from avito_service.normalization import normalize_listing
from avito_service.service import AvitoAnalysisService


def raw_listing(identifier, city):
    slug = "yaroslavl" if city == "Ярославль" else "moskva"
    return {
        "id": str(identifier), "title": "Sony PlayStation 5 Slim", "price": 50_000,
        "url": f"https://www.avito.ru/{slug}/igry/ps5_{identifier}", "location": city,
        "status": "active", "description": "Консоль PS5 Slim, полный комплект.",
        "images": ["https://01.img.avito.st/offline.jpg"],
        "parameters": {"Модель": "PlayStation 5 Slim", "Состояние": "Отличное"},
    }


class RecordingReviewer:
    def __init__(self):
        self.text_ids = []

    def begin_budget(self, _budget):
        pass

    def review_text(self, listing, _request):
        self.text_ids.append(listing.listing_id)
        return AIReview(
            listing_id=listing.listing_id, text_analyzed=True, matches_request=False,
            verdict=ReviewVerdict.REJECT, confidence=0.9,
        )


class CitySourceTests(unittest.TestCase):
    def test_yaroslavl_url_keeps_the_actual_query_and_required_storage(self):
        request = SearchRequest("PS5 Slim", location="Ярославль", required_storage="1 TB", max_results=200, pickup_only=True)
        payload = ZenStudioProvider(ServiceConfig(apify_max_charge_usd=2, report_max_cost_rub=250))._actor_input(request)
        url = urlparse(payload["searchUrl"])
        self.assertEqual((url.scheme, url.hostname, url.path), ("https", "www.avito.ru", "/yaroslavl"))
        self.assertEqual(parse_qs(url.query), {"q": ["PS5 Slim 1 TB"]})
        self.assertEqual(payload["maxResults"], 200)

    def test_unverified_city_slug_is_not_invented(self):
        payload = ZenStudioProvider(ServiceConfig())._actor_input(SearchRequest("PS5", location="Другой город"))
        self.assertNotIn("searchUrl", payload)
        self.assertEqual(payload["location"], "Другой город")

    def test_source_url_does_not_replace_the_final_city_gate(self):
        item = normalize_listing({"id": "7654321", "title": "Sony PlayStation 5", "price": 40000,
            "url": "https://www.avito.ru/moskva/igry/ps5_7654321", "location": "Москва"})
        self.assertFalse(matches_listing_request(item, SearchRequest("PS5", location="Ярославль", pickup_only=True)))

    def test_foreign_and_unknown_city_are_removed_before_paid_text_review(self):
        reviewer = RecordingReviewer()
        service = AvitoAnalysisService(None, reviewer, ai_max_cost_rub=10, report_max_cost_rub=10)
        rows = [raw_listing(7654321, "Ярославль"), raw_listing(7654322, "Москва")]
        unknown = raw_listing(7654323, "Москва")
        unknown.pop("location")
        unknown["url"] = "https://www.avito.ru/rossiya/igry/ps5_7654323"
        report = service.analyze_dataset(rows + [unknown], SearchRequest(
            "PS5", location="Ярославль", pickup_only=True,
        ))
        self.assertEqual(reviewer.text_ids, ["7654321"])
        self.assertEqual(report.pipeline.collected_count, 3)
        self.assertEqual(report.pipeline.correct_city_count, 1)
        self.assertEqual(report.pipeline.basic_filtered_count, 1)


if __name__ == "__main__":
    unittest.main()
