from __future__ import annotations

import hmac
import logging
import os
import threading
from functools import wraps
from typing import Iterable, Optional
from urllib.parse import urlencode

from flask import Flask, Response, abort, jsonify, request
from plivo import plivoxml
from plivo.utils.signature_v3 import validate_v3_signature

import prompts
from config import Config, load_config

log = logging.getLogger("ivr")

LANGUAGE_BY_DIGIT = {"1": "en", "2": "es"}


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

    def current_lang() -> Optional[str]:
        lang = request.args.get("lang")
        return lang if lang in prompts.TEXT else None

    @app.before_request
    def verify_plivo_signature():
        if not request.path.startswith("/ivr/") or not config.validate_signature:
            return None
        signature = request.headers.get("X-Plivo-Signature-V3")
        nonce = request.headers.get("X-Plivo-Signature-V3-Nonce")
        if not signature or not nonce:
            log.warning("Rejected %s: missing Plivo signature headers", request.path)
            abort(403)
        uri = config.base_url + request.full_path.rstrip("?")
        params = request.form.to_dict() if request.method == "POST" else {}
        if not validate_v3_signature(request.method, uri, nonce, config.auth_token,
                                     signature, params):
            log.warning("Rejected %s: invalid Plivo signature", request.path)
            abort(403)
        return None

    def require_auth(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not sessions.is_authenticated(call_uuid()):
                log.warning("[%s] Unauthenticated request to %s -> OTP", call_uuid(), request.path)
                response = plivoxml.ResponseElement()
                response.add(redirect(url("/ivr/otp/prompt")))
                return xml(response)
            return view(*args, **kwargs)
        return wrapper

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
            response.add(redirect(url("/ivr/language/prompt")))
            return xml(response)

        log.info("[%s] OTP incorrect (attempt %d, %d digits entered)",
                 call_uuid(), attempt, len(entered))
        return xml(otp_menu(preface=prompts.OTP_WRONG))

    def language_menu(preface: Optional[str] = None) -> plivoxml.ResponseElement:
        elements = [speak(preface)] if preface else []
        elements += [speak(prompts.LANGUAGE_PROMPT_EN, "en"),
                     speak(prompts.LANGUAGE_PROMPT_ES, "es")]
        return gather(url("/ivr/language/select"), 1, elements, url("/ivr/language/prompt"))

    @app.post("/ivr/language/prompt")
    @require_auth
    def language_prompt():
        return xml(language_menu())

    @app.post("/ivr/language/select")
    @require_auth
    def language_select():
        lang = LANGUAGE_BY_DIGIT.get(digits())
        if lang is None:
            log.info("[%s] Invalid language option %r", call_uuid(), digits())
            return xml(language_menu(preface=prompts.INVALID))

        log.info("[%s] Language selected: %s", call_uuid(), lang)
        response = plivoxml.ResponseElement()
        response.add(speak(prompts.TEXT[lang]["selected"], lang))
        response.add(redirect(url("/ivr/menu/prompt", lang=lang)))
        return xml(response)

    def action_menu(lang: str, preface: Optional[str] = None) -> plivoxml.ResponseElement:
        text = prompts.TEXT[lang]
        elements = [speak(preface, lang)] if preface else []
        elements.append(speak(text["menu"], lang))
        return gather(url("/ivr/menu/select", lang=lang), 1, elements,
                      url("/ivr/menu/prompt", lang=lang), lang)

    def back_to_language_menu() -> Response:
        response = plivoxml.ResponseElement()
        response.add(redirect(url("/ivr/language/prompt")))
        return xml(response)

    @app.post("/ivr/menu/prompt")
    @require_auth
    def menu_prompt():
        lang = current_lang()
        if lang is None:
            return back_to_language_menu()
        return xml(action_menu(lang))

    @app.post("/ivr/menu/select")
    @require_auth
    def menu_select():
        lang = current_lang()
        if lang is None:
            return back_to_language_menu()
        text = prompts.TEXT[lang]
        choice = digits()
        response = plivoxml.ResponseElement()

        if choice == "1":
            log.info("[%s] Playing audio message (%s)", call_uuid(), lang)
            response.add(speak(text["audio_intro"], lang))
            response.add(plivoxml.PlayElement(config.audio_url))
            response.add(speak(text["back_to_menu"], lang))
            response.add(redirect(url("/ivr/menu/prompt", lang=lang)))
            return xml(response)

        if choice == "2":
            log.info("[%s] Forwarding to associate %s", call_uuid(), config.associate_number)
            response.add(speak(text["connecting"], lang))
            dial = plivoxml.DialElement(
                action=url("/ivr/dial/status", lang=lang),
                method="POST",
                caller_id=config.plivo_number,
                timeout=30,
                redirect=True,
            )
            dial.add(plivoxml.NumberElement(config.associate_number))
            response.add(dial)
            return xml(response)

        log.info("[%s] Invalid menu option %r", call_uuid(), choice)
        return xml(action_menu(lang, preface=text["invalid"]))

    @app.post("/ivr/dial/status")
    @require_auth
    def dial_status():
        lang = current_lang() or "en"
        text = prompts.TEXT[lang]
        status = request.values.get("DialStatus", "")
        log.info("[%s] Dial to associate finished: %s", call_uuid(), status)

        response = plivoxml.ResponseElement()
        if status == "completed":
            response.add(speak(text["goodbye"], lang))
            response.add(plivoxml.HangupElement())
        else:
            response.add(speak(text["unavailable"], lang))
            response.add(redirect(url("/ivr/menu/prompt", lang=lang)))
        return xml(response)

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
