from __future__ import annotations

import hmac
import logging
import os
import threading
from typing import Iterable, Optional
from urllib.parse import urlencode

from flask import Flask, Response, jsonify, request
from plivo import plivoxml

import prompts
from config import Config, load_config

log = logging.getLogger("ivr")


class CallSessions:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, dict] = {}

    def _entry(self, call_uuid: str) -> dict:
        return self._calls.setdefault(call_uuid, {"authenticated": False, "otp_attempts": 0})

    def start(self, call_uuid: str) -> None:
        with self._lock:
            self._calls[call_uuid] = {"authenticated": False, "otp_attempts": 0}

    def record_otp_attempt(self, call_uuid: str) -> int:
        with self._lock:
            entry = self._entry(call_uuid)
            entry["otp_attempts"] += 1
            return entry["otp_attempts"]

    def authenticate(self, call_uuid: str) -> None:
        with self._lock:
            self._entry(call_uuid)["authenticated"] = True

    def is_authenticated(self, call_uuid: str) -> bool:
        with self._lock:
            return self._calls.get(call_uuid, {}).get("authenticated", False)

    def end(self, call_uuid: str) -> None:
        with self._lock:
            self._calls.pop(call_uuid, None)


def xml(response: plivoxml.ResponseElement) -> Response:
    return Response(response.to_string(), mimetype="application/xml")


def speak(text: str, lang: str = "en") -> plivoxml.SpeakElement:
    return plivoxml.SpeakElement(text, **prompts.VOICES[lang])


def redirect(url: str) -> plivoxml.RedirectElement:
    return plivoxml.RedirectElement(url, method="POST")


def gather(action: str, num_digits: int, elements: Iterable, reprompt_url: str,
           lang: str = "en") -> plivoxml.ResponseElement:
    response = plivoxml.ResponseElement()
    get_input = plivoxml.GetInputElement(
        action=action,
        method="POST",
        input_type="dtmf",
        num_digits=num_digits,
        digit_end_timeout=5,
        execution_timeout=15,
        redirect=True,
    )
    for element in elements:
        get_input.add(element)
    response.add(get_input)
    response.add(speak(prompts.TEXT[lang]["no_input"], lang))
    response.add(redirect(reprompt_url))
    return response


def create_app(config: Optional[Config] = None) -> Flask:
    config = config or load_config()
    app = Flask(__name__)
    sessions = CallSessions()
    app.extensions["call_sessions"] = sessions

    def url(path: str, **query: str) -> str:
        full = f"{config.base_url}{path}"
        return f"{full}?{urlencode(query)}" if query else full

    def call_uuid() -> str:
        return request.values.get("CallUUID", "unknown")

    def digits() -> str:
        return request.values.get("Digits", "").strip()

    def otp_menu(preface: Optional[str] = None) -> plivoxml.ResponseElement:
        elements = [speak(preface)] if preface else []
        elements.append(speak(prompts.OTP_PROMPT))
        return gather(url("/ivr/otp/verify"), 4, elements, url("/ivr/otp/prompt"))

    @app.post("/ivr/answer")
    def answer():
        sessions.start(call_uuid())
        log.info("[%s] Call answered (from=%s, to=%s)", call_uuid(),
                 request.values.get("From"), request.values.get("To"))
        return xml(otp_menu(preface=prompts.WELCOME))

    @app.post("/ivr/otp/prompt")
    def otp_prompt():
        return xml(otp_menu())

    @app.post("/ivr/otp/verify")
    def otp_verify():
        entered = digits()
        attempt = sessions.record_otp_attempt(call_uuid())
        if hmac.compare_digest(entered.encode(), config.otp.encode()):
            sessions.authenticate(call_uuid())
            log.info("[%s] OTP correct (attempt %d)", call_uuid(), attempt)
            response = plivoxml.ResponseElement()
            response.add(speak(prompts.OTP_OK))
            response.add(plivoxml.HangupElement())
            return xml(response)

        log.info("[%s] OTP incorrect (attempt %d, %d digits entered)",
                 call_uuid(), attempt, len(entered))
        return xml(otp_menu(preface=prompts.OTP_WRONG))

    @app.post("/ivr/hangup")
    def hangup():
        log.info("[%s] Call ended (cause=%s, duration=%ss)", call_uuid(),
                 request.values.get("HangupCause"), request.values.get("Duration"))
        sessions.end(call_uuid())
        return "OK", 200

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    create_app().run(host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
