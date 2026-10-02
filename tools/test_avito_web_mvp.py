from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig
from avito_service.errors import SearchCancelledError
from avito_service.http_api import make_server
from avito_service.jobs import SearchJobRegistry
from avito_service.models import SearchRequest


class _BlockingService:
    def __init__(self) -> None:
        self.started = Event()

    def search(self, search, *, deadline_seconds, progress):
        self.started.set()
        while True:
            progress("collect", "Собираем рынок…", 8)
            time.sleep(0.01)


class AvitoWebMVPTests(unittest.TestCase):
    def test_registry_cancel_is_idempotent_and_never_publishes_partial_result(self) -> None:
        service = _BlockingService()
        registry = SearchJobRegistry(service)
        job = registry.start(SearchRequest("PlayStation 5"))
        self.assertTrue(service.started.wait(timeout=1))

        self.assertIs(registry.cancel(job.job_id), job)
        self.assertIs(registry.cancel(job.job_id), job)
        for _ in range(100):
            if job.worker_finished:
                break
            time.sleep(0.01)

        snapshot = job.snapshot()
        self.assertTrue(job.worker_finished)
        self.assertEqual(snapshot["state"], "cancelled")
        self.assertEqual(snapshot["errorCode"], "SEARCH_CANCELLED")
        self.assertNotIn("result", snapshot)
        self.assertIn("createdAt", snapshot)

    def test_http_cancel_route_and_unknown_job_are_public_safe(self) -> None:
        config = ServiceConfig(
            apify_token="fixture-token",
            ai_api_key="fixture-ai",
            ai_base_url="https://ai.example/v1",
            ai_model="vision-model",
        )
        service = _BlockingService()
        with TemporaryDirectory() as directory:
            server = make_server(
                "127.0.0.1", 0, service=service, config=config,
                support_path=Path(directory) / "support.jsonl",
            )
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                create = Request(
                    f"{base}/api/avito/jobs",
                    data=json.dumps({"query": "PlayStation 5"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(create, timeout=3) as response:
                    job = json.loads(response.read().decode())
                self.assertTrue(service.started.wait(timeout=1))

                cancel = Request(
                    f"{base}/api/avito/jobs/{job['jobId']}/cancel",
                    data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
                )
                with urlopen(cancel, timeout=3) as response:
                    cancelled = json.loads(response.read().decode())
                self.assertEqual(cancelled["state"], "cancelled")
                connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request(
                    "POST", f"/api/avito/jobs/{job['jobId']}/cancel", body="{}",
                    headers={"Content-Type": "application/json", "Content-Length": "2"},
                )
                repeated_response = connection.getresponse()
                repeated = json.loads(repeated_response.read().decode())
                self.assertEqual(repeated["state"], "cancelled")
                connection.request("GET", f"/api/avito/jobs/{job['jobId']}")
                status_response = connection.getresponse()
                status = json.loads(status_response.read().decode())
                connection.close()
                self.assertEqual(status_response.status, 200)
                self.assertEqual(status["state"], "cancelled")

                missing = Request(
                    f"{base}/api/avito/jobs/not-a-real-job/cancel",
                    data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(missing, timeout=3)
                payload = json.loads(caught.exception.read().decode())
                self.assertEqual(caught.exception.code, 404)
                self.assertEqual(payload["code"], "JOB_NOT_FOUND")
                self.assertNotIn("traceback", json.dumps(payload).casefold())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_apify_known_run_is_aborted_after_cancel(self) -> None:
        class FixtureProvider(ZenStudioProvider):
            def __init__(self) -> None:
                super().__init__(ServiceConfig(apify_token="fixture-token"))
                self.started = False
                self.aborted = ""

            def _json_request(self, method, url, *, payload=None, timeout=130):
                self.started = True
                return {"data": {"id": "run-123", "status": "RUNNING"}}

            def _abort_run(self, run_id: str) -> None:
                self.aborted = run_id

        provider = FixtureProvider()
        with self.assertRaises(SearchCancelledError):
            provider._collect_payload_once(
                {}, 1, time.monotonic() + 30, 0.1, {}, lambda: provider.started,
            )
        self.assertEqual(provider.aborted, "run-123")


if __name__ == "__main__":
    unittest.main()
