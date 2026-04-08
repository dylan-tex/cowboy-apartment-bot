from flask import Flask, request, jsonify
import os
import json
import anthropic
from twilio.rest import Client
import requests
import smtplib
import threading
from email.mime.text import MIMEText


app = Flask(__name__)


# Clients
anthropic_client = anthropic.Anthropic(api_key=os.environ["CLAUDE_API_KEY"])
twilio_client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])


# In-memory conversation stor
conversations = {}


SYSTEM_PROMPT = """You are Melissa, a friendly and warm apartment locator assistant for Cowboy Apartment Locators. Your personality is inviting, upbeat, and customer-service focused — like a helpful friend in the real estate world.

Your job is to collect the following information from the customer, one or two questions at a time so it feels like a natural conversation:
1. What type of place they're looking for (apartment, house, condo, etc.)
2. Number of bedrooms
3. Number of bathrooms
4. Preferred location/area
5. Monthly budget
6. Move-in date
7. Credit situation (excellent, good, fair, poor — reassure them all situations are welcome)
8. Their phone number
9. When they're available to tour

Rules:
- Always introduce yourself as Melissa with Cowboy Apartment Locators on the first message.
- Be warm, conversational, and encouraging. Never robotic.
- Ask no more than 2 questions at once.
- Once you have all 9 pieces of information, provide a friendly closing summary of what you've collected and let them know a locator will reach out soon.
- The summary message should include the word LEAD_COMPLETE on its own line at the end (hidden signal — do not mention this to the customer).
"""


def send_sms_alert(lead_summary):
    try:
        twilio_client.messages.create(
            body=lead_summary,
            from_=os.environ["TWILIO_PHONE_NUMBER"],
            to=os.environ["ALERT_PHONE_NUMBER"]
        )
    except Exception as e:
        print(f"SMS error: {e}")


def send_email_alert(lead_summary):
    try:
        emails = ["dylansilver3@gmail.com", "matthew.gies@live.com"]
        msg = MIMEText(lead_summary)
        msg["Subject"] = "🤠 New Lead - Cowboy Apartment Locators"
        msg["From"] = os.environ["EMAIL_ADDRESS"]
        msg["To"] = ", ".join(emails)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(os.environ["EMAIL_ADDRESS"], os.environ["EMAIL_APP_PASSWORD"])
            server.sendmail(os.environ["EMAIL_ADDRESS"], emails, msg.as_string())
        print("Email alert sent successfully")
    except Exception as e:
        print(f"Email error: {e}")


def send_alerts_background(lead_summary):
    """Fire SMS and email in a background thread so the webhook returns instantly."""
    t = threading.Thread(target=_send_all_alerts, args=(lead_summary,), daemon=True)
    t.start()


def _send_all_alerts(lead_summary):
    send_sms_alert(lead_summary)
    send_email_alert(lead_summary)


def build_lead_summary(conversation_history):
    try:
        summary_prompt = """Based on this conversation, extract the lead information and format it as a clean summary with these fields:
- Property Type:
- Bedrooms/Bathrooms:
- Location:
- Budget:
- Move-in Date:
- Credit:
- Phone:
- Tour Availability:
- Name (if given):

Conversation:
"""
        messages_text = ""
        for msg in conversation_history:
            role = "Customer" if msg["role"] == "user" else "Melissa"
            messages_text += role + ": " + msg["content"] + "\n"

        response = anthropic_client.messages.create(
            model="claude-opus-4-5",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": summary_prompt + messages_text
            }]
        )
        return response.content[0].text
    except Exception as e:
        print(f"Summary error: {e}")
        return "New lead received - check conversation log"


def send_facebook_message(recipient_id, message_text):
    url = f"https://graph.facebook.com/v18.0/me/messages"
    headers = {"Content-Type": "application/json"}
    params = {"access_token": os.environ["FB_PAGE_ACCESS_TOKEN"]}
    data = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text}
    }
    response = requests.post(url, headers=headers, params=params, json=data)
    return response.json()


def get_claude_response(user_message, conversation_history):
    messages = conversation_history + [{"role": "user", "content": user_message}]

    response = anthropic_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=messages
    )
    return response.content[0].text


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == os.environ["WEBHOOK_VERIFY_TOKEN"]:
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.get_json()

    if data.get("object") != "page":
        return jsonify({"status": "ignored"}), 200

    for entry in data.get("entry", []):
        for messaging in entry.get("messaging", []):
            sender_id = messaging.get("sender", {}).get("id")
            message = messaging.get("message", {})
            message_text = message.get("text", "")

            if not message_text or not sender_id:
                continue

            # Skip messages sent by the page itself
            page_id = entry.get("id")
            if sender_id == page_id:
                continue

            # Get or create conversation
            if sender_id not in conversations:
                conversations[sender_id] = []

            conversation_history = conversations[sender_id]

            # Get Claude's response
            claude_response = get_claude_response(message_text, conversation_history)

            # Update conversation history
            conversation_history.append({"role": "user", "content": message_text})
            conversation_history.append({"role": "assistant", "content": claude_response})

            # Check if lead is complete
            if "LEAD_COMPLETE" in claude_response:
                clean_response = claude_response.replace("LEAD_COMPLETE", "").strip()
                send_facebook_message(sender_id, clean_response)

                # Build summary and fire alerts in background (non-blocking)
                summary = build_lead_summary(conversation_history)
                send_alerts_background(summary)

                # Reset conversation
                conversations[sender_id] = []
            else:
                send_facebook_message(sender_id, claude_response)

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(debug=True)
