"""Run the standalone local Avito review application."""
from __future__ import annotations

import argparse

from .http_api import make_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Avito collection and multimodal review service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    server = make_server(args.host, args.port)
    ready = server.service_config.readiness()
    print(f"Avito Review: http://{args.host}:{args.port}/")
    print(f"Readiness: Apify={ready['apify']} AI={ready['ai']}")
    print(f"AI model: {server.service_config.ai_model}; source limit: "
          f"{server.service_config.safe_apify_listing_limit}; "
          f"AI budget: {server.service_config.effective_ai_budget_rub:g} RUB")
    print(f"Live pilot telemetry: {'enabled' if server.service_config.live_pilot else 'disabled'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
