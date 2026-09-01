from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.pilot_sample import PilotManifestError, aggregate_real_request_pilot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a privacy-safe Search V2 pilot report")
    parser.add_argument("manifest", type=Path, help="Path to a real-request pilot JSON manifest")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = aggregate_real_request_pilot(payload)
    except (OSError, json.JSONDecodeError, PilotManifestError) as exc:
        print(f"Invalid pilot manifest: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
