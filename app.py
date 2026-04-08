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

Start by warmly greeting the customer and asking what type of place they're looking for.

Once you have collected ALL 9 pieces of information, output ONLY a structured summary in this exact format (nothing else after it):

LEAD_COMPLETE
Name: [Facebook name if known, else Unknown]
Type: [apartment/house/condo/etc]
Bedrooms: [number]
Bathrooms: [number]
Location: [area]
Budget: [amount]
Move-in: [date]
Credit: [situation]
Phone: [number]
Availability: [when]

Do not output LEAD_COMPLETE until you have all 9 pieces of information."""


def send_sms_alert(lead_summary):
        try:
                    twilio_client.messages.create(
                                    body="New Lead:\n" + lead_summary,
                                    from_=os.environ["TWILIO_PHONE_NUMBER"],
                                    to=os.environ["ALERT_PHONE_NUMBER"]
                    )
except Exception as e:
        print("SMS error:", e)


def send_email_alert(lead_summary):
        try:
                    sg = sendgrid.SendGridAPIClient(api_key=os.environ["SENDGRID_API_KEY"])
                    emails = ["dylansilver3@gmail.com", "matthew.gies@live.com"]
                    for email in emails:
                                    message = Mail(
                                                        from_email="advertising@dylansilver.org",
                                                        to_emails=email,
                                                        subject="New Lead - Cowboy Apartment Locators",
                                                        plain_text_content=lead_summary
                                    )
                                    sg.send(message)
        except Exception as e:
                    print("Email error:", e)


def send_alerts_background(summary):
        thread = threading.Thread(target=_send_alerts, args=(summary,))
        thread.daemon = True
        thread.start()


def _send_alerts(summary):
        send_sms_alert(summary)
        send_email_alert(summary)


def build_lead_summary(messages):
        try:
                    conversation_text = ""
                    for msg in messages:
                                    role = msg["role"]
                                    conversation_text += role + ": " + msg["content"] + "\n"

                    response = anthropic_client.messages.create(
                        model="claude-3-haiku-20240307",
                        max_tokens=500,
                        messages=[{
                            "role": "user",
                            "content": "Extract the lead info from this conversation and format it nicely:\n\n" + conversation_text
                        }]
                    )
                    return response.content[0].text
except Exception as e:
        return "Error building summary: " + str(e)


def get_ai_response(user_message, conversation_history):
        conversation_history.append({
                    "role": "user",
                    "content": user_message
        })

    response = anthropic_client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=1000,
                system=SYSTEM_PROMPT,
                messages=conversation_history
    )

    assistant_message = response.content[0].text
    conversation_history.append({
                "role": "assistant",
                "content": assistant_message
    })

    return assistant_message, conversation_history


def send_facebook_message(recipient_id, message_text):
        url = "https://graph.facebook.com/v18.0/me/messages"
        headers = {"Content-Type": "application/json"}
        params = {"access_token": os.environ["FB_PAGE_ACCESS_TOKEN"]}

    chunks = [message_text[i:i+2000] for i in range(0, len(message_text), 2000)]
    for chunk in chunks:
                data = {
                                "recipient": {"id": recipient_id},
                                "message": {"text": chunk}
                }
                requests.post(url, headers=headers, params=params, json=data)


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
                return jsonify({"status": "ok"}), 200

    for entry in data.get("entry", []):
                for messaging in entry.get("messaging", []):
                                sender_id = messaging["sender"]["id"]
                                if "message" in messaging and "text" in messaging["message"]:
                                                    user_message = messaging["message"]["text"]

                                    if sender_id not in conversations:
                                                            conversations[sender_id] = []

                                    ai_response, updated_history = get_ai_response(user_message, conversations[sender_id])
                                    conversations[sender_id] = updated_history

                        if "LEAD_COMPLETE" in ai_response:
                                                summary = build_lead_summary(updated_history)
                                                send_alerts_background(summary)
                                                clean_response = ai_response.replace("LEAD_COMPLETE", "").strip()
                                                lines = [l for l in clean_response.split("\n") if not l.startswith(("Name:", "Type:", "Bedrooms:", "Bathrooms:", "Location:", "Budget:", "Move-in:", "Credit:", "Phone:", "Availability:"))]
                                                clean_response = "\n".join(lines).strip()
                                                if clean_response:
                                                                            send_facebook_message(sender_id, clean_response)
                                                                        send_facebook_message(sender_id, "Thanks so much! One of our locators will be in touch with you shortly. We're excited to help you find your perfect place!")
                    conversations[sender_id] = []
else:
                    send_facebook_message(sender_id, ai_response)

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
        app.run(debug=True)
