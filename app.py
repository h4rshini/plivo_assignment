from __future__ import annotations

import logging
import os
from typing import Optional

from flask import Flask, Response, jsonify, request
from plivo import plivoxml

from config import Config, load_config

log = logging.getLogger("ivr")


def xml(response: plivoxml.ResponseElement) -> Response:
    return Response(response.to_string(), mimetype="application/xml")


def speak(text: str) -> plivoxml.SpeakElement:
    return plivoxml.SpeakElement(text, voice="WOMAN", language="en-US")


def create_app(config: Optional[Config] = None) -> Flask:
    config = config or load_config()
    app = Flask(__name__)

    def call_uuid() -> str:
        return request.values.get("CallUUID", "unknown")

    @app.post("/ivr/answer")
    def answer():
        log.info("[%s] Call answered (from=%s, to=%s)", call_uuid(),
                 request.values.get("From"), request.values.get("To"))
        response = plivoxml.ResponseElement()
        response.add(speak("Hello from InspireWorks. Your call is connected."))
        response.add(plivoxml.HangupElement())
        return xml(response)

    @app.post("/ivr/hangup")
    def hangup():
        log.info("[%s] Call ended (cause=%s, duration=%ss)", call_uuid(),
                 request.values.get("HangupCause"), request.values.get("Duration"))
        return "OK", 200

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    create_app().run(host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
