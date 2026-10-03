"""Read-only local evidence inventory for unresolved Apify reservations.

No credentials, network, SpendingGuard mutations, or automatic reconciliation.
Date/cap matches are leads only; they never authorize reducing a reservation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
MOSCOW = timezone(timedelta(hours=3))
RUN_ID = re.compile(r"^[A-Za-z0-9]{17}$")
STATUSES = {"READY", "RUNNING", "SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTING", "ABORTED"}
SKIP_DIRS = {".venv", ".git", "__pycache__", "node_modules", "qa_deps", "qa_parser_deps"}


def _amount(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _day(stamp):
    if not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(MOSCOW).date().isoformat() if parsed.tzinfo else None


def _objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def audit(runtime: Path, ledger: Path) -> dict:
    runtime = runtime.resolve()
    ledger = ledger.resolve()
    original = ledger.read_bytes()
    state = json.loads(original.decode("utf-8-sig"))
    if state.get("schema") != 1 or not isinstance(state.get("records"), dict):
        raise ValueError("Unsupported ledger; no reconciliation allowed")
    pending = {key: row for key, row in state["records"].items() if row.get("settled_units") is None}
    mentions = {key: [] for key in pending}
    observations = {}
    failures = []
    scanned = 0
    for folder, dirs, files in os.walk(runtime):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith("qa_")]
        for name in sorted(files):
            path = Path(folder) / name
            if (path == ledger or path == runtime / "avito_spend.json"
                    or path.suffix.lower() not in {".json", ".md"}
                    or name.startswith(("avito_reservation_audit", "spend-before-terminal-reclassification"))):
                continue
            relative = path.relative_to(runtime).as_posix()
            try:
                content = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                failures.append({"source": relative, "error": type(exc).__name__})
                continue
            scanned += 1
            for reservation in pending:
                if reservation in content:
                    mentions[reservation].append(relative)
            if path.suffix.lower() != ".json":
                continue
            try:
                document = json.loads(content)
            except json.JSONDecodeError:
                failures.append({"source": relative, "error": "JSONDecodeError"})
                continue
            for node in _objects(document):
                identity = node.get("run_id")
                if not identity and isinstance(node.get("status"), str) and node["status"] in STATUSES:
                    identity = node.get("id")
                if not isinstance(identity, str) or not RUN_ID.fullmatch(identity):
                    continue
                started = node.get("startedAt") or node.get("started_at")
                day = _day(started)
                # Folder dates are weaker leads, never a provider timestamp.
                folder_stamp = re.search(r"(?:^|/)(20\d{6})T", relative)
                inferred_day = None
                if day is None and folder_stamp:
                    inferred_day = datetime.strptime(folder_stamp[1], "%Y%m%d").date().isoformat()
                observation = {
                    "source": relative, "status": node.get("status"),
                    "started_at": started, "provider_local_date": day,
                    "folder_inferred_date": inferred_day,
                    "actual_cost_usd": _amount(node.get("usageTotalUsd", node.get("actual_cost_usd"))),
                    "requested_cap_usd": _amount(node.get("requested_max_total_charge_usd")),
                }
                if observation not in observations.setdefault(identity, []):
                    observations[identity].append(observation)

    reservations = []
    for identity, row in pending.items():
        amount = row["reserved_units"] / 100_000_000
        leads = []
        for run_id, seen in observations.items():
            same_day = [item for item in seen if (item["provider_local_date"] or item["folder_inferred_date"]) == row["date"]]
            if not same_day:
                continue
            caps = {item["requested_cap_usd"] for item in same_day if item["requested_cap_usd"] is not None}
            cap_matches = any(math.isclose(cap, amount, rel_tol=0, abs_tol=0.00000002) for cap in caps)
            leads.append({
                "run_id": run_id,
                "evidence_grade": "same_day_and_cap_only" if cap_matches else "same_day_only",
                "cap_matches": cap_matches,
                "recorded_as_run_in_ledger": "run:" + run_id in state["records"],
                "observations": same_day,
            })
        leads.sort(key=lambda item: (not item["cap_matches"], item["run_id"]))
        reservations.append({
            "reservation_id": identity, "date": row["date"], "reserved_usd": amount,
            "explicit_mentions_outside_ledger": mentions[identity],
            "candidate_runs": leads, "safe_to_reduce": False,
            "reason": "Reservation has no immutable run_id binding; date/cap similarity is insufficient.",
        })
    grouped = Counter()
    for row in pending.values():
        grouped[row["date"]] += row["reserved_units"]
    return {
        "mode": "offline_read_only", "network_requests": 0, "ledger_writes": 0,
        "ledger_sha256": hashlib.sha256(original).hexdigest(),
        "ledger_unchanged_during_audit": ledger.read_bytes() == original,
        "files_scanned": scanned, "read_failures": failures,
        "pending_count": len(pending),
        "pending_reserved_usd": sum(row["reserved_units"] for row in pending.values()) / 100_000_000,
        "pending_by_date_usd": {day: units / 100_000_000 for day, units in sorted(grouped.items())},
        "unique_observed_run_count": len(observations),
        "reservations": reservations,
        "safe_reconciliation_plan": [
            "Keep every unresolved reservation unchanged until evidence links it to an exact run or proves no paid run exists.",
            "Obtain complete authenticated Actor run history for each affected local day, with pagination, run IDs and provider timestamps; GET only.",
            "If complete account history covers all pending dates and proves every provider run terminal, a separately reviewed active-to-settled-estimate migration may retain each full reserved amount unchanged; it proves termination, not an individual charge or zero cost.",
            "Fetch receipts and saved inputs for candidate runs, and identify the original request/reservation binding. Matching only amount or date does not prove identity.",
            "If a binding is proven, prepare a separate reviewed mapping: reservation_id, run_id, terminal status, final usageTotalUsd, evidence paths and ledger hash.",
            "Apply settle(final=True) only from that proof, with locking and a fresh ledger snapshot. Never release unknown billing as zero.",
            "If exact historical binding remains impossible, retain every monetary obligation as unknown; fresh account balance alone cannot identify which pending run is already billed.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / "runtime")
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args(argv)
    result = audit(args.runtime, args.ledger or args.runtime / "avito_spend.json")
    # ASCII makes a redirected Windows console reliable; files are never written.
    sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
