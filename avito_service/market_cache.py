"""Bounded, expendable reference snapshots; no contacts or provider credentials."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from threading import Lock
import time

from .models import AIReview, AnalyzedListing, NormalizedListing, ReviewVerdict, SearchRequest, SellerSummary
from .matching import canonical_text
from .request_intent import parse_request_signature
from .risk_rules import evaluate_rules


class MarketSnapshotCache:
    def __init__(self, path: Path | None = None, *, ttl_seconds: int = 900, max_entries: int = 64):
        self.path = path
        self.ttl_seconds = min(900, max(1, ttl_seconds))
        self.max_entries = max(1, max_entries)
        self._entries: dict[str, tuple[float, tuple[AnalyzedListing, ...]]] = {}
        self._lock = Lock()

    @staticmethod
    def key(request: SearchRequest, reviewer_version: str = "") -> str:
        signature = parse_request_signature(request)
        if signature.requested_family != "unknown":
            market_identity = "|".join((
                signature.requested_product,
                signature.requested_family,
                signature.requested_form_factor,
                signature.requested_edition,
                signature.requested_storage,
            ))
        else:
            market_identity = canonical_text(request.query)
        value = {
            "schema": 6, "reviewer": reviewer_version,
            "market_identity": market_identity,
            "location": canonical_text(request.location),
            "category": request.category, "storage": request.required_storage,
            "sim": request.required_sim, "condition": request.required_condition,
            "attributes": sorted(request.attributes),
            "pickup_only": request.pickup_only,
        }
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def get(self, key: str) -> tuple[AnalyzedListing, ...] | None:
        items, _ = self.lookup(key)
        return items

    def lookup(self, key: str) -> tuple[tuple[AnalyzedListing, ...] | None, dict[str, object]]:
        """Return cached items plus non-sensitive hit/age telemetry."""
        with self._lock:
            entry = self._entries.get(key)
            source = "memory"
            if entry is None and self.path is not None:
                try:
                    target = self.path / f"{key}.json"
                    if not target.exists():
                        return None, {"hit": False, "age_seconds": None, "source": "disk",
                                      "miss_reason": "not_found"}
                    raw = json.loads(target.read_text(encoding="utf-8"))
                    if raw.get("version") != 3:
                        return None, {"hit": False, "age_seconds": None, "source": "disk",
                                      "miss_reason": "schema_mismatch"}
                    if float(raw["expires"]) < time.time():
                        return None, {"hit": False, "age_seconds": None, "source": "disk",
                                      "miss_reason": "expired"}
                    items = []
                    for item in raw["items"]:
                        listing_data = item["listing"]
                        listing_data["seller"] = SellerSummary(**listing_data["seller"])
                        for field in ("images", "badges"):
                            listing_data[field] = tuple(listing_data[field])
                        listing_data["verified_at"] = ""
                        listing_data["verification_status"] = "unverified"
                        listing = NormalizedListing(**listing_data)
                        review_data = item["review"]
                        review_data["verdict"] = ReviewVerdict(review_data["verdict"])
                        for field in ("photo_coverage", "description_findings", "photo_findings",
                                      "defects", "price_conditions", "conflicts", "manual_checks"):
                            review_data[field] = tuple(review_data.get(field, ()))
                        review = AIReview(**review_data)
                        items.append(AnalyzedListing(listing, evaluate_rules(listing), review))
                    entry = (float(raw["expires"]), tuple(items))
                    source = "disk"
                except (OSError, ValueError, TypeError, KeyError):
                    return None, {"hit": False, "age_seconds": None, "source": "disk",
                                  "miss_reason": "invalid"}
            if entry is None or entry[0] < time.time():
                self._entries.pop(key, None)
                return None, {"hit": False, "age_seconds": None, "source": source,
                              "miss_reason": "expired" if entry is not None else "not_found"}
            fresh = tuple(item for item in entry[1] if item.listing.is_recently_collected(self.ttl_seconds))
            result = fresh or None
            age = max(0.0, self.ttl_seconds - max(0.0, entry[0] - time.time()))
            return result, {
                "hit": result is not None,
                "age_seconds": round(age, 3) if result is not None else None,
                "source": source,
                "miss_reason": None if result is not None else "stale_items",
            }

    def put(self, key: str, items: tuple[AnalyzedListing, ...]) -> None:
        if not items:
            return
        expires = time.time() + self.ttl_seconds
        with self._lock:
            if len(self._entries) >= self.max_entries and key not in self._entries:
                oldest = min(self._entries, key=lambda item: self._entries[item][0])
                self._entries.pop(oldest, None)
            self._entries[key] = (expires, items)
            if self.path is None:
                return
            try:
                self.path.mkdir(parents=True, exist_ok=True)
                target = self.path / f"{key}.json"
                files = list(self.path.glob("*.json"))
                if len(files) >= self.max_entries and not target.exists():
                    # Only an enumerated direct cache child can be removed.
                    oldest = min(files, key=lambda item: item.stat().st_mtime)
                    if oldest.resolve().parent == self.path.resolve():
                        oldest.unlink()
                records = []
                for item in items:
                    listing = asdict(item.listing)
                    # Diagnostics and scraped descriptions can include arbitrary
                    # text; the cache contains no raw profile/phone response.
                    listing["verified_at"] = ""
                    listing["verification_status"] = "unverified"
                    records.append({"listing": listing, "review": asdict(item.ai_review)})
                temporary = target.with_suffix(".tmp")
                temporary.write_text(json.dumps({"version": 3, "expires": expires, "items": records},
                                                 ensure_ascii=False), encoding="utf-8")
                temporary.replace(target)
            except OSError:
                # Cache failure cannot break a recommendation or bypass checks.
                pass
