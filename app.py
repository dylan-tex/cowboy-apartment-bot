from flask import Flask, request, jsonify
import os
import json
import anthropic
from twilio.rest import Client
import requests
import threading
import sendgrid
from sendgrid.helpers.mail import Mail

app = Flask(__name__)

# Clients
anthropic_client = anthropic.Anthropic(api_key=os.environ["CLAUDE_API_KEY"])
twilio_client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])

# In-memory conversation store
conversations = {}

SYSTEM_PROMPT = """You are Melissa, a friendly and warm apartment locator assistant for Cowboy Apartment Locators. Your personality is inviting, upbeat, and customer-service focused.

Collect this info one or two questions at a time:
1. Type of place (apartment, house, condo, etc.)
2. Number of bedrooms
3. Number of bathrooms
4. Preferred location/area
5. Monthly budget
6. Move-in date
7. Credit situation (excellent, good, fair, poor)
8. Their phone number
9. When they are available to tour

Start by warmly greeting the customer.

Once you have ALL 9 pieces of information, end your final message with exactly:
LEAD_COMPLETE"""


def send_sms_alert(lead_summary):
    try:
        twilio_client.messages.create(
            body=lead_summary,
            from_=os.environ["TWILIO_PHONE_NUMBER"],
            to=os.environ["ALERT_PHONE_NUMBER"]
        )
    except Exception as e:
        print("SMS error:", e)


def send_email_alert(lead_summary):
    try:
        sg = sendgrid.SendGridAPIClient(api_key=os.environ["SENDGRID_API_KEY"])
        emails = ["dylansilver3@gmail.com", "matthew.gies@live.com"]
        for recipient in emails:
            message = Mail(
                from_email=os.environ.get("EMAIL_ADDRESS", "advertising@dylansilver.org"),
                to_emails=recipient,
                subject="New Lead - Cowboy Apartment Locators",
                plain_text_content=lead_summary
            )
            sg.send(message)
    except Exception as e:
        print("Email error:", e)


def send_alerts_background(summary)
    send_sms_alert(summary)
    send_email_alert(summary)


def build_lead_summary(messages):
    try:
        messages_text = ""
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            messages_text += role + ": " + content + "\n"
        response = anthropic_client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": "Extract lead info:\n\n" + messages_text
            }]
        )
        return response.content[0].text
    except Exception as e:
        print("Summary error:", e)
        return "New lead received - check conversation logs"


def get_or_create_conversation(sender_id):
    if sender_id not in conversations:
        conversations[sender_id] = []
    return conversations[sender_id]


def send_facebook_message(recipient_id, message_text):
    url = "https://graph.facebook.com/v18.0/me/messages"
    headers = {"Content-Type": "application/json"}
    params = {"access_token": os.environ["FB_PAGE_ACCESS_TOKEN"]}
    data = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text}
    }
    try:
        response = requests.post(url, headers=headers, params=params, json=data)
        response.raise_for_status()
    except Exception as e:
        print("Facebook send error:", e)


def get_claude_response(conversation_history):
    try:
        response = anthropic_client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=conversation_history
        )
        return response.content[0].text
    except Exception as e:
        print("Claude error:", e)
        return "I am having trouble connecting right now. Please try again!"


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
    try:
        data = request.get_json()
        if data.get("object") != "page":
            return jsonify({"status": "not a page event"}), 200
        for entry in data.get("entry", []):
            for messaging in entry.get("messaging", []):
                sender_id = messaging.get("sender", {}).get("id")
                message = messaging.get("message", {})
                message_text = message.get("text", "")
                if not sender_id or not message_text:
                    continue
                conversation = get_or_create_conversation(sender_id)
                conversation.append({"role": "user", "content": message_text})
                assistant_response = get_claude_response(conversation)
                conversation.append({"role": "assistant", "content": assistant_response})
                if "LEAD_COMPLETE" in assistant_response:
                    clean_response = assistant_response.replace("LEAD_COMPLETE", "").strip()
                    send_facebook_message(sender_id, clean_response)
                    summary = build_lead_summary(conversation)
                    thread = threading.Thread(target=send_alerts_background, args=(summary,))
                    thread.daemon = True
                    thread.start()
                    del conversations[sender_id]
                else:
                    send_facebook_message(sender_id, assistant_response)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print("Webhook error:", e)
        return jsonify({"status": "error"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
