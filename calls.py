from __future__ import annotations

import logging

import plivo
import requests
from plivo import exceptions as plivo_exceptions

from config import Config

log = logging.getLogger(__name__)


class CallError(Exception):
    pass


def place_call(config: Config, to_number: str) -> str:
    client = plivo.RestClient(auth_id=config.auth_id, auth_token=config.auth_token)
    try:
        response = client.calls.create(
            from_=config.plivo_number,
            to_=to_number,
            answer_url=f"{config.base_url}/ivr/answer",
            answer_method="POST",
            hangup_url=f"{config.base_url}/ivr/hangup",
            hangup_method="POST",
            ring_timeout=45,
        )
    except plivo_exceptions.PlivoRestError as exc:
        raise CallError(f"Plivo rejected the call: {exc}") from exc
    except requests.RequestException as exc:
        raise CallError(f"Could not reach the Plivo API: {exc}") from exc

    log.info("Outbound call queued to %s (request_uuid=%s)", to_number, response.request_uuid)
    return response.request_uuid
