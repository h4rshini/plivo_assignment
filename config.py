from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

OTP = "1912"

DEFAULT_COUNTRY_CODE = "91"
DEFAULT_AUDIO_URL = "https://s3.amazonaws.com/plivocloud/Trumpet.mp3"

E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
REQUIRED_VARIABLES = ("PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN", "PLIVO_NUMBER",
                      "ASSOCIATE_NUMBER", "BASE_URL")


class ConfigError(Exception):
    pass


def normalize_number(raw: str, country_code: str = DEFAULT_COUNTRY_CODE) -> str:
    number = re.sub(r"[\s\-().]", "", raw or "")
    if number.startswith("+"):
        pass
    elif number.startswith("00"):
        number = "+" + number[2:]
    elif number.startswith("0"):
        number = f"+{country_code}{number[1:]}"
    elif len(number) == 10:
        number = f"+{country_code}{number}"
    else:
        number = "+" + number

    if not E164_PATTERN.match(number):
        raise ConfigError(f"Invalid phone number: {raw!r}")
    return number


def validate_otp(otp: str) -> str:
    if len(otp) != 4 or not otp.isdigit():
        raise ConfigError("OTP must be exactly 4 digits (DDMM).")
    try:
        datetime.strptime(otp + "2000", "%d%m%Y")
    except ValueError as exc:
        raise ConfigError(f"OTP {otp!r} is not a valid DDMM date.") from exc
    return otp


@dataclass(frozen=True)
class Config:
    auth_id: str
    auth_token: str
    plivo_number: str
    associate_number: str
    base_url: str
    my_number: Optional[str] = None
    audio_url: str = DEFAULT_AUDIO_URL
    validate_signature: bool = True
    otp: str = OTP


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


def load_config() -> Config:
    load_dotenv()

    missing = [name for name in REQUIRED_VARIABLES if not os.getenv(name)]
    if missing:
        raise ConfigError(
            f"Missing environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill it in."
        )

    base_url = os.environ["BASE_URL"].strip().rstrip("/")
    if not base_url.startswith(("https://", "http://")):
        raise ConfigError("BASE_URL must be a public http(s) URL, e.g. your ngrok URL.")

    my_number = os.getenv("MY_PHONE_NUMBER")
    return Config(
        auth_id=os.environ["PLIVO_AUTH_ID"].strip(),
        auth_token=os.environ["PLIVO_AUTH_TOKEN"].strip(),
        plivo_number=normalize_number(os.environ["PLIVO_NUMBER"]),
        associate_number=normalize_number(os.environ["ASSOCIATE_NUMBER"]),
        base_url=base_url,
        my_number=normalize_number(my_number) if my_number else None,
        audio_url=os.getenv("AUDIO_URL", DEFAULT_AUDIO_URL).strip(),
        validate_signature=env_flag("VALIDATE_SIGNATURE", True),
        otp=validate_otp(OTP),
    )