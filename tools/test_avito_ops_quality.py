"""Offline regressions for protected paid work, job recovery, and owner previews."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from avito_service.config import ServiceConfig
from avito_service.errors import AvitoServiceError, ConfigurationError
from avito_service.http_api import make_server
from avito_service.jobs import SearchJob, SearchJobRegistry, WORKER_DEADLINE_SECONDS
from avito_service.models import SearchRequest


OWNER = "fixture-owner-key-" + "x" * 32
ACCESS = "fixture-customer-key-" + "y" * 32
CONFIG = ServiceConfig(apify_token="fixture-apify", ai_api_key="fixture-ai", ai_base_url="https://example.invalid", ai_model="fixture")


class FixtureReport:
    admin_warnings = ("owner-only warning",)

    def public_dict(self, *, include_admin=False):
        result = {"recommendations": [{"listing": {"id": "safe"}}], "warnings": []}
        if include_admin:
            result.update({"adminRecommendations": [{"listing": {"id": "unverified-owner-preview"}}], "adminCosts": {"aiCostRub": 7}, "adminAudit": {"textCompleted": 1}})
        return result


class FixtureService:
    def __init__(self, *, blocking=False):
        self.calls = 0
        self.started = Event()
        self.release = Event()
        self.deadline = None
        if not blocking:
            self.release.set()

    def search(self, search, *, deadline_seconds, progress):
        self.calls += 1
        self.deadline = deadline_seconds
        self.started.set()
        self.release.wait(timeout=3)
        return FixtureReport()

    def analyze_dataset(self, listings, search, *, deadline_at, started_at, progress):
        self.calls += 1
        self.deadline = deadline_at - started_at
        self.started.set()
        self.release.wait(timeout=3)
        return FixtureReport()


def finished(job):
    for _ in range(200):
        if job.worker_finished:
            return
        time.sleep(0.005)
    raise AssertionError("fixture worker did not finish")


@contextmanager
def http_fixture(*, blocking=False, protected=False):
    service = FixtureService(blocking=blocking)
    with TemporaryDirectory() as temporary:
        server = make_server("127.0.0.1", 0, service=service, config=CONFIG,
                             support_path=Path(temporary) / "support.jsonl", owner_token=OWNER,
                             access_token=ACCESS if protected else "")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, service, f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            service.release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def request(base, path, payload=None, *, token="", headers=None):
    current_headers = dict(headers or {})
    if token:
        current_headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        current_headers["Content-Type"] = "application/json"
    req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                  headers=current_headers, method="POST" if payload is not None else "GET")
    try:
        response = urlopen(req, timeout=3)
    except HTTPError as response:
        return response.code, json.loads(response.read())
    with response:
        return response.status, json.loads(response.read())


class JobQualityTests(unittest.TestCase):
    def test_soft_target_does_not_end_job_or_discard_late_result(self):
        now = time.monotonic()
        job = SearchJob("soft", now - 50, now + 130)
        self.assertEqual(job.snapshot()["state"], "running")
        self.assertTrue(job.snapshot()["softTargetExceeded"])
        registry = SearchJobRegistry(FixtureService())
        # A request observer can time out; the worker's completed report still wins.
        job.deadline_at = now - 1
        self.assertEqual(job.snapshot()["state"], "timeout")
        registry._run(job, SearchRequest("iPhone 15"))
        self.assertEqual(job.snapshot()["state"], "complete")
        self.assertEqual(job.snapshot()["result"]["recommendations"][0]["listing"]["id"], "safe")
        self.assertEqual(registry.service.deadline, WORKER_DEADLINE_SECONDS)

    def test_public_and_owner_payloads_keep_the_same_verified_candidates(self):
        registry = SearchJobRegistry(FixtureService())
        job = registry.start(SearchRequest("iPhone 15"))
        finished(job)
        public = job.snapshot()["result"]
        owner = job.snapshot(include_admin=True)["result"]
        self.assertNotIn("adminCosts", public)
        self.assertNotIn("adminAudit", public)
        self.assertNotIn("adminWarnings", public)
        self.assertEqual(public["recommendations"], owner["recommendations"])
        self.assertEqual(owner["adminWarnings"], ["owner-only warning"])
        self.assertEqual(owner["adminCosts"]["aiCostRub"], 7)

    def test_duplicate_inflight_work_does_not_spend_another_quota_slot(self):
        service = FixtureService(blocking=True)
        registry = SearchJobRegistry(service, max_starts_per_hour=1)
        search = SearchRequest("iPhone 15")
        job = registry.start(search)
        self.assertTrue(service.started.wait(timeout=1))
        self.assertIs(registry.start(search), job)
        self.assertEqual(len(registry._starts), 1)
        service.release.set()
        finished(job)
        with self.assertRaises(AvitoServiceError) as caught:
            registry.start_dataset(SearchRequest("iPhone 16"), [{"id": "fixture"}])
        self.assertEqual(caught.exception.code, "SEARCH_RATE_LIMIT")
        self.assertEqual(service.calls, 1)


class HTTPQualityTests(unittest.TestCase):
    def test_public_binding_and_weak_tokens_fail_before_listening(self):
        for host, token in (("0.0.0.0", ""), ("127.0.0.1", "weak")):
            with self.subTest(host=host, token=bool(token)), self.assertRaises(ConfigurationError):
                make_server(host, 0, service=FixtureService(), config=CONFIG, owner_token=token, access_token="")

    def test_owner_query_string_never_grants_owner_access(self):
        with http_fixture() as (server, service, base):
            code, body = request(base, "/api/session?owner=1")
            self.assertEqual(code, 200)
            self.assertFalse(body["owner"])
            code, body = request(base, "/api/avito/analyze-dataset?owner=1", {"listings": [{}]})
            self.assertEqual(code, 401)
            self.assertEqual(body["code"], "OWNER_AUTH_REQUIRED")
            self.assertEqual(service.calls, 0)

    def test_owner_auth_required_for_dataset_and_preview_polling(self):
        with http_fixture() as (server, service, base):
            code, created = request(base, "/api/avito/analyze-dataset", {"search": {"query": "iPhone 15"}, "listings": [{"id": 1}]}, token=OWNER)
            self.assertEqual(code, 202)
            job = server.jobs.get(created["jobId"])
            finished(job)
            code, body = request(base, "/api/avito/jobs/" + job.job_id)
            self.assertEqual(code, 401)
            code, body = request(base, "/api/avito/jobs/" + job.job_id, token=OWNER)
            self.assertEqual(code, 200)
            self.assertTrue(body["result"]["preview"])
            self.assertEqual(body["result"]["adminRecommendations"][0]["listing"]["id"], "unverified-owner-preview")
            self.assertNotIn("unverified-owner-preview", json.dumps(body["result"]["recommendations"]))

    def test_all_paid_routes_share_concurrency_gate(self):
        with http_fixture(blocking=True) as (server, service, base):
            server.jobs.max_active = 1
            code, first = request(base, "/api/avito/search", {"query": "iPhone 15"})
            self.assertEqual(code, 202)
            self.assertTrue(service.started.wait(timeout=1))
            code, duplicate = request(base, "/api/avito/jobs", {"query": "iPhone 15"})
            self.assertEqual(code, 202)
            self.assertEqual(duplicate["jobId"], first["jobId"])
            for path, payload in (("/api/avito/search", {"query": "iPhone 16"}), ("/api/avito/analyze-dataset", {"search": {"query": "iPhone 17"}, "listings": [{}]})):
                code, body = request(base, path, payload, token=OWNER)
                self.assertEqual(code, 429)
                self.assertEqual(body["code"], "SEARCH_BUSY")
            self.assertEqual(service.calls, 1)

    def test_hourly_cost_quota_is_enforced_on_search_alias(self):
        with http_fixture() as (server, service, base):
            server.jobs.max_starts_per_hour = 1
            code, created = request(base, "/api/avito/jobs", {"query": "iPhone 15"})
            finished(server.jobs.get(created["jobId"]))
            code, body = request(base, "/api/avito/search", {"query": "iPhone 16"})
            self.assertEqual(code, 429)
            self.assertEqual(body["code"], "SEARCH_RATE_LIMIT")
            self.assertEqual(service.calls, 1)

    def test_protected_customer_cannot_read_owner_payload(self):
        with http_fixture(protected=True) as (server, service, base):
            self.assertEqual(request(base, "/api/avito/jobs", {"query": "iPhone 15"})[0], 401)
            code, created = request(base, "/api/avito/jobs", {"query": "iPhone 15"}, token=ACCESS)
            self.assertEqual(code, 202)
            job = server.jobs.get(created["jobId"])
            finished(job)
            self.assertEqual(request(base, "/api/avito/jobs/" + job.job_id)[0], 401)
            code, public = request(base, "/api/avito/jobs/" + job.job_id + "?owner=1", token=ACCESS)
            self.assertEqual(code, 200)
            self.assertNotIn("adminCosts", public["result"])
            code, owner = request(base, "/api/avito/jobs/" + job.job_id, token=OWNER)
            self.assertIn("adminCosts", owner["result"])
            self.assertEqual(owner["result"]["recommendations"], public["result"]["recommendations"])
            self.assertNotIn(OWNER, json.dumps(owner))

    def test_foreign_origin_or_rebound_hostname_cannot_start_paid_work(self):
        with http_fixture() as (server, service, base):
            for headers in ({"Origin": "https://foreign.invalid"}, {"Host": "foreign.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
                code, body = request(base, "/api/avito/jobs", {"query": "iPhone 15"}, headers=headers)
                self.assertEqual(code, 403)
                self.assertEqual(body["code"], "FOREIGN_ORIGIN")
            self.assertEqual(service.calls, 0)


if __name__ == "__main__":
    unittest.main()
