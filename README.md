# Cowboy Apartment Bot

A Flask webhook service for Cowboy Apartment Locators' Facebook Messenger assistant, Melissa. It uses Claude to collect apartment-search preferences and contact information, replies through the Facebook Graph API, and sends lead notifications through SendGrid and Twilio.

## How it works

1. Facebook sends Page messaging events to `POST /webhook`.
2. The service keeps each sender's conversation in memory and asks Claude for a reply.
3. A separate Claude extraction checks for a name, email, phone number, and city to trigger an early lead email.
4. When the assistant returns `LEAD_COMPLETE`, the service summarizes the conversation and starts background SMS and email notifications.
5. The conversation remains available for follow-up questions. Sending the exact text `**start over**`, including the asterisks, resets that sender's conversation and tracking.

The assistant does not query property listings or real-time availability. Its prompt directs listing questions to the locating team.

## Technology

Python, Flask, the Anthropic SDK, Twilio, SendGrid, Requests, and Gunicorn. Dependency versions are pinned in [requirements.txt](requirements.txt). The model identifier used in [app.py](app.py) is `claude-haiku-4-5`.

## Local setup

Use Python 3 with virtual environment support:

```sh
git clone https://github.com/dylan-tex/cowboy-apartment-bot.git
cd cowboy-apartment-bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Set the following environment variables in your shell or hosting service:

| Variable | Used for |
| --- | --- |
| `CLAUDE_API_KEY` | Anthropic client; required at application import. |
| `TWILIO_ACCOUNT_SID` | Twilio client; required at application import. |
| `TWILIO_AUTH_TOKEN` | Twilio client; required at application import. |
| `FB_PAGE_ACCESS_TOKEN` | Sending Messenger replies. |
| `WEBHOOK_VERIFY_TOKEN` | Matching Facebook's webhook verification request; choose your own value. |
| `TWILIO_PHONE_NUMBER` | SMS sending number. |
| `ALERT_PHONE_NUMBER` | SMS alert recipient. |
| `SENDGRID_API_KEY` | Sending early and completed lead emails. |
| `EMAIL_ADDRESS` | Optional SendGrid sending address; defaults to the address in `app.py`. |
| `PORT` | Optional development-server port; defaults to `5000`. |

[.env.example](.env.example) lists only some of these variables. The app does not load `.env` files automatically. If you use a local environment file, add the missing values, export it into your shell, and keep it uncommitted. Email recipients are currently set directly in `app.py`.

Start the development server after exporting your configuration:

```sh
python app.py
```

The service listens on port 5000 by default. It has no homepage or chat UI.

## Webhook verification and usage

With the app running and `WEBHOOK_VERIFY_TOKEN` exported, check the verification route without triggering a conversation:

```sh
curl --get 'http://127.0.0.1:5000/webhook' \
  --data-urlencode 'hub.mode=subscribe' \
  --data-urlencode "hub.verify_token=$WEBHOOK_VERIFY_TOKEN" \
  --data-urlencode 'hub.challenge=local-check'
```

A matching token returns `local-check`; a mismatch returns HTTP 403.

For Messenger integration, expose the service at a public HTTPS address, use `/webhook` as the Facebook Page webhook callback, configure the same verification token, and subscribe the Page to messaging events. Text messages are processed; events without a sender ID or text are skipped. Configured message handling makes external API calls and can send actual alerts.

## Deployment and state

[Procfile](Procfile) declares the production entry point:

```text
web: gunicorn app:app
```

Use a Python host that installs `requirements.txt`, runs the Gunicorn entry point, supplies the environment variables, and exposes the application over HTTPS.

Conversations and notification tracking live in process memory. Restarting the process clears them, and multiple workers do not share them. Notification threads also run inside the web process. This repository contains no persistent conversation database or durable job queue.

## Files and checks

- [app.py](app.py): Flask routes, assistant prompt, conversation state, and integrations.
- [requirements.txt](requirements.txt): Python dependencies.
- [Procfile](Procfile): Gunicorn process definition.
- [.env.example](.env.example): Partial configuration reference.

No automated test suite is included. A lightweight syntax check is `python -m py_compile app.py`; the GET verification request above checks local routing without sending messages.
