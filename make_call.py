from __future__ import annotations

import argparse
import logging
import sys

import requests

from calls import CallError, place_call
from config import ConfigError, load_config, normalize_number


def main() -> int:
    parser = argparse.ArgumentParser(description="Place the InspireWorks IVR demo call.")
    parser.add_argument("--to", help="Number to call (defaults to MY_PHONE_NUMBER)")
    args = parser.parse_args()

    try:
        config = load_config()
        raw_to = args.to or config.my_number
        if not raw_to:
            raise ConfigError("No number to call: pass --to or set MY_PHONE_NUMBER in .env")
        to_number = normalize_number(raw_to)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    health_url = f"{config.base_url}/health"
    try:
        requests.get(health_url, timeout=5,
                     headers={"ngrok-skip-browser-warning": "true"}).raise_for_status()
    except requests.RequestException as exc:
        print(f"Webhook server not reachable at {health_url}: {exc}\n"
              "Is `python app.py` running and is ngrok forwarding to it?", file=sys.stderr)
        return 1

    try:
        request_uuid = place_call(config, to_number)
    except CallError as exc:
        print(f"Call failed: {exc}", file=sys.stderr)
        return 1

    print(f"Calling {to_number} from {config.plivo_number}. Request UUID: {request_uuid}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(main())
