# InspireWorks IVR Demo (Plivo Voice API)

An outbound-call IVR built on the Plivo Voice API and Plivo XML. It demonstrates:

1. **Outbound call:** the Plivo number calls your phone (CLI or web page).
2. **OTP authentication:** you enter a 4-digit OTP (a birthdate in DDMM format) over DTMF. A wrong OTP re-prompts until the correct one is entered.
3. **Level 1 menu:** press 1 for English, 2 for Spanish.
4. **Level 2 menu:** press 1 to play an MP3, 2 to be forwarded to a live associate.

Invalid or missing input repeats the current prompt at every level.

## How it works

```
make_call.py / web UI ──► Plivo REST API: Call.create(from=PLIVO_NUMBER, to=YOUR_PHONE,
                                                      answer_url=BASE_URL/ivr/answer)
                                   │ you answer
                                   ▼
Plivo ◄──XML──► Flask webhooks (app.py), exposed publicly through ngrok

 /ivr/answer ─► OTP <GetInput numDigits=4> ─► /ivr/otp/verify ─(wrong)─► re-prompt
                                                  │ correct
                                                  ▼
 /ivr/language/prompt ─► /ivr/language/select ─(1=en / 2=es)─► /ivr/menu/prompt?lang=..
                                                                    │
                                     /ivr/menu/select?lang=.. ◄─────┘
                                       ├─ 1 ─► <Play> MP3 ─► back to menu
                                       └─ 2 ─► <Dial><Number>associate ─► /ivr/dial/status
```

Each step is a webhook that returns Plivo XML. `<GetInput inputType="dtmf">` collects digits and POSTs them (`Digits`) to the next step's `action` URL. If nothing is pressed, Plivo falls through to a `<Redirect>` that repeats the same prompt.

| File | Purpose |
|---|---|
| `app.py` | Flask webhook server: IVR state machine, Plivo signature validation, trigger web page |
| `calls.py` | Starts the outbound call with the Plivo REST API |
| `make_call.py` | CLI trigger with a pre-flight check that the webhook server is reachable |
| `config.py` | Loads and validates settings from `.env`; holds the hardcoded OTP |
| `prompts.py` | All spoken text (English/Spanish), separate from the flow logic |
| `tests/` | Tests that simulate Plivo's webhook calls through the whole flow |

### Design decisions
- **Credentials only in environment variables.** `.env` is git-ignored; `.env.example` documents the keys.
- **Webhook security.** Every `/ivr/*` request's `X-Plivo-Signature-V3` header is verified with the Auth Token, so only Plivo can drive the call flow.
- **OTP enforced on the server.** Per-call state (keyed by `CallUUID`) records whether the caller passed OTP. Menu endpoints redirect unauthenticated calls back to the OTP prompt, so the IVR can't be skipped by calling a later URL directly. The OTP is compared in constant time (`hmac.compare_digest`).
- **Stateless language handling.** The chosen language travels in the webhook URL (`?lang=es`), and Spanish prompts use Spanish TTS (`es-US`).
- **Graceful failure.** Invalid keys and timeouts repeat the prompt. If the associate is busy or doesn't answer, the caller hears a message and returns to the menu.
- **Phone number normalization.** Local Indian formats (`02264236412`, `98765 43210`) are converted to E.164.

## Setup

**Prerequisites:** Python 3.9+, a Plivo account (Auth ID, Auth Token, a voice-enabled number), and [ngrok](https://ngrok.com/download) with a free static domain.

```bash
git clone https://github.com/h4rshini/plivo_assignment.git && cd plivo_assignment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

| Variable | Description |
|---|---|
| `PLIVO_AUTH_ID` / `PLIVO_AUTH_TOKEN` | From the Plivo console overview page |
| `PLIVO_NUMBER` | Plivo number the call is made from |
| `ASSOCIATE_NUMBER` | Number that option 2 forwards to |
| `MY_PHONE_NUMBER` | Your phone, which receives the call |
| `BASE_URL` | Public ngrok URL (see below) |
| `AUDIO_URL` | Publicly accessible MP3 for option 1 |
| `VALIDATE_SIGNATURE` | `true` to reject webhooks without a valid Plivo signature |

The OTP is hardcoded in `config.py` as `OTP = "1912"` (19 December, DDMM).

## Run

```bash
ngrok http --url=https://<your-domain>.ngrok-free.dev 127.0.0.1:8000
```
Set `BASE_URL` in `.env` to the same https URL, then in a second terminal:
```bash
python app.py
```
And in a third:
```bash
python make_call.py
python make_call.py --to +91XXXXXXXXXX
```
Or open `http://127.0.0.1:8000/` and click **Call me**.

## Test

```bash
pytest -q        # simulates Plivo's webhooks through every branch of the flow
```

Manual test script (this is also the demo video flow):
1. Run `python make_call.py` and answer the call.
2. Enter a **wrong** OTP. You hear "incorrect" and are asked again.
3. Enter the **correct** OTP. You hear "verified".
4. Press **9**. You hear "not a valid option" and the language menu repeats. Then press **1** (English) or **2** (Spanish).
5. Press **1**. The MP3 plays and you return to the menu.
6. Press **2**. The call is forwarded to the associate number.

The server log shows each step with the call's UUID.

## Troubleshooting
- **`Address already in use`:** another copy of `app.py` is running. Stop it with `lsof -ti :8000 | xargs kill`.
- **Call never arrives:** on a Plivo trial account, the destination must be a verified number (Console → Phone Numbers → Sandbox Numbers).
- **"Application error" or silence after answering:** check that `BASE_URL` matches your ngrok URL and that both `app.py` and ngrok are running.
- **403 in the server log:** a signature mismatch, usually because `BASE_URL` doesn't match the URL Plivo called. As a last resort, set `VALIDATE_SIGNATURE=false` for local debugging.
- **No audio on option 1:** open `AUDIO_URL` in a browser. It must be a public, direct link to an MP3.
