import pytest
from plivo.utils.signature_v3 import construct_post_url, get_signature_v3

from app import create_app
from config import Config, ConfigError, normalize_number, validate_otp

BASE = "https://example.ngrok-free.app"
UUID = "call-123"


def make_config(**overrides):
    values = dict(
        auth_id="TESTAUTHID",
        auth_token="test-token",
        plivo_number="+918035454161",
        associate_number="+912264236412",
        base_url=BASE,
        audio_url="https://example.com/audio.mp3",
        validate_signature=False,
        otp="1503",
    )
    values.update(overrides)
    return Config(**values)


@pytest.fixture
def client():
    return create_app(make_config()).test_client()


def post(client, path, **form):
    form.setdefault("CallUUID", UUID)
    return client.post(path, data=form).get_data(as_text=True)


def authenticate(client):
    post(client, "/ivr/answer")
    post(client, "/ivr/otp/verify", Digits="1503")



def test_answer_prompts_for_4_digit_otp(client):
    body = post(client, "/ivr/answer")
    assert 'numDigits="4"' in body
    assert f'action="{BASE}/ivr/otp/verify"' in body
    assert "one time password" in body


def test_wrong_otp_reprompts_and_stays_locked(client):
    post(client, "/ivr/answer")
    body = post(client, "/ivr/otp/verify", Digits="0000")
    assert "incorrect" in body and 'numDigits="4"' in body
    # Still locked out of the menu:
    assert "/ivr/otp/prompt" in post(client, "/ivr/language/prompt")


def test_wrong_then_correct_otp(client):
    post(client, "/ivr/answer")
    post(client, "/ivr/otp/verify", Digits="1111")
    body = post(client, "/ivr/otp/verify", Digits="1503")
    assert "verified" in body and f"{BASE}/ivr/language/prompt" in body


def test_no_input_repeats_prompt(client):
    body = post(client, "/ivr/answer")
    assert f"{BASE}/ivr/otp/prompt</Redirect>" in body



def test_language_menu_is_bilingual(client):
    authenticate(client)
    body = post(client, "/ivr/language/prompt")
    assert 'language="en-US"' in body and 'language="es-US"' in body


@pytest.mark.parametrize("digit,lang", [("1", "en"), ("2", "es")])
def test_language_selection(client, digit, lang):
    authenticate(client)
    body = post(client, "/ivr/language/select", Digits=digit)
    assert f"/ivr/menu/prompt?lang={lang}" in body


def test_invalid_language_repeats_menu(client):
    authenticate(client)
    body = post(client, "/ivr/language/select", Digits="7")
    assert "not a valid option" in body and "/ivr/language/select" in body



def test_spanish_menu_uses_spanish_voice(client):
    authenticate(client)
    body = post(client, "/ivr/menu/prompt?lang=es")
    assert 'language="es-US"' in body and "Oprima" in body


def test_option_1_plays_audio_then_returns_to_menu(client):
    authenticate(client)
    body = post(client, "/ivr/menu/select?lang=en", Digits="1")
    assert "<Play>https://example.com/audio.mp3</Play>" in body
    assert "/ivr/menu/prompt?lang=en</Redirect>" in body


def test_option_2_dials_associate(client):
    authenticate(client)
    body = post(client, "/ivr/menu/select?lang=es", Digits="2")
    assert "<Number>+912264236412</Number>" in body
    assert 'callerId="+918035454161"' in body
    assert "/ivr/dial/status?lang=es" in body


def test_invalid_menu_option_repeats_menu(client):
    authenticate(client)
    body = post(client, "/ivr/menu/select?lang=en", Digits="9")
    assert "not a valid option" in body and "/ivr/menu/select?lang=en" in body


def test_missing_language_goes_back_to_level_1(client):
    authenticate(client)
    assert "/ivr/language/prompt" in post(client, "/ivr/menu/select", Digits="1")


@pytest.mark.parametrize("status,expected", [("completed", "<Hangup"),
                                             ("no-answer", "not available")])
def test_dial_status(client, status, expected):
    authenticate(client)
    assert expected in post(client, "/ivr/dial/status?lang=en", DialStatus=status)


def test_hangup_clears_session(client):
    authenticate(client)
    post(client, "/ivr/hangup", HangupCause="NORMAL_CLEARING")
    assert "/ivr/otp/prompt" in post(client, "/ivr/language/prompt")



def test_signature_required_when_enabled():
    client = create_app(make_config(validate_signature=True)).test_client()
    assert client.post("/ivr/answer", data={"CallUUID": UUID}).status_code == 403


def test_valid_signature_accepted():
    client = create_app(make_config(validate_signature=True)).test_client()
    params, nonce = {"CallUUID": UUID, "From": "+918035454161"}, "12345"
    base = construct_post_url(f"{BASE}/ivr/menu/prompt?lang=en", dict(params)).decode()
    signature = get_signature_v3(b"test-token", base, b"12345").decode()
    response = client.post("/ivr/menu/prompt?lang=en", data=params, headers={
        "X-Plivo-Signature-V3": signature, "X-Plivo-Signature-V3-Nonce": nonce})
    assert response.status_code == 200



@pytest.mark.parametrize("raw,expected", [
    ("02264236412", "+912264236412"),
    ("9876543210", "+919876543210"),
    ("+91 98765-43210", "+919876543210"),
    ("+918035454161", "+918035454161"),
])
def test_normalize_number(raw, expected):
    assert normalize_number(raw) == expected


@pytest.mark.parametrize("bad", ["3202", "150", "abcd", "1513"])
def test_invalid_otp_rejected(bad):
    with pytest.raises(ConfigError):
        validate_otp(bad)


def test_dashboard_is_local_only(client):
    assert client.get("/").status_code == 200
    assert client.get("/", headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 404
    assert client.post("/api/call", json={}, headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 404


def test_api_call_rejects_invalid_number(client):
    response = client.post("/api/call", json={"to": "12"})
    assert response.status_code == 400 and response.get_json()["ok"] is False


def test_timeline_records_each_step(client):
    authenticate(client)
    post(client, "/ivr/language/select", Digits="2")
    kinds = [event["kind"] for event in client.get("/api/events").get_json()["events"]]
    assert kinds == ["answered", "otp_ok", "language"]
    assert client.get("/api/events?after=3").get_json()["events"] == []
