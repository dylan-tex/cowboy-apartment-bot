from flask import Flask, request, jsonify
import os
import json
import anthropic
from twilio.rest import Client
import requests
import smtplib
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
- If they ask WHY you need certain info, explain naturally. For example: budget helps narrow down options so you don't waste their time, credit helps identify which properties they'd qualify for, etc.
- If they ask about specific apartment locations, complexes, pricing details, or how the locator process works, say: "Great question! For specifics like that, you can text us directly at 210-975-9200 and we'll get you taken care of!"
- Once you have collected ALL 9 pieces of information, end your message with the exact token: ##LEAD_COMPLETE##
- Do not include ##LEAD_COMPLETE## until you truly have all 9 pieces of info.
- Keep messages concise — this is a Facebook Messenger chat, not an email."""

def get_conversation(sender_id):
        if sender_id not in conversations:
                    conversations[sender_id] = []
                return conversations[sender_id]

def send_facebook_message(recipient_id, message_text):
        url = "https://graph.facebook.com/v18.0/me/messages"
    params = {"access_token": os.environ["FB_PAGE_ACCESS_TOKEN"]}
    data = {
                "recipient": {"id": recipient_id},
                "message": {"text": message_text}
    }
    response = requests.post(url, params=params, json=data)
    return response.json()

def send_sms_alert(lead_summary):
        numbers = ["2107758193", "2109759200"]
    for number in numbers:
                twilio_client.messages.create(
                                body=lead_summary,
                                from_=os.environ["TWILIO_PHONE_NUMBER"],
                                to=f"+1{number}"
                )

def send_email_alert(lead_summary):
        emails = ["dylansilver3@gmail.com"]
    msg = MIMEText(lead_summary)
    msg["Subject"] = "New Lead - Cowboy Apartment Locators"
    msg["From"] = os.environ["EMAIL_ADDRESS"]
    msg["To"] = ", ".join(emails)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(os.environ["EMAIL_ADDRESS"], os.environ["EMAIL_APP_PASSWORD"])
                server.sendmail(os.environ["EMAIL_ADDRESS"], emails, msg.as_string())

def get_claude_response(sender_id, user_message):
        history = get_conversation(sender_id)
    history.append({"role": "user", "content": user_message})

    response = anthropic_client.messages.create(
                model="claude-opus-4-5",
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=history
    )

    assistant_message = response.content[0].text
    history.append({"role": "assistant", "content": assistant_message})
    conversations[sender_id] = history
    return assistant_message

def build_lead_summary(sender_id, history):
        # Use Claude to extract structured lead info from the conversation
        try:
                    conversation_text = ""
                    for msg in history:
                                    role = "Customer" if msg["role"] == "user" else "Melissa"
                                    conversation_text += f"{role}: {msg['content']}\n"

                    extraction_response = anthropic_client.messages.create(
                        model="claude-opus-4-5",
                        max_tokens=300,
                        system="Extract lead info from this apartment locator conversation. Return ONLY this format with no extra text:\nName: [name or Unknown]\nPhone: [phone or Not provided]\nType: [apartment/house/condo/etc]\nBeds/Baths: [X bd/X ba]\nArea: [location]\nBudget: [$ amount]\nMove-in: [date]\nCredit: [excellent/good/fair/poor]\nTour: [availability]",
                        messages=[{"role": "user", "content": conversation_text}]
                    )
                    lead_info = extraction_response.content[0].text.strip()
except Exception:
        lead_info = "Could not extract lead details."

    summary = "NEW LEAD - Cowboy Apartment Locators\n\n" + lead_info
    return summary[:1500]

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

    if data.get("object") == "page":
                for entry in data.get("entry", []):
                                for event in entry.get("messaging", []):
                                                    sender_id = event["sender"]["id"]

                                    if "message" in event and "text" in event["message"]:
                                                            user_text = event["message"]["text"]
                                                            reply = get_claude_response(sender_id, user_text)

                                        if "##LEAD_COMPLETE##" in reply:
                                                                    clean_reply = reply.replace("##LEAD_COMPLETE##", "").strip()
                                                                    send_facebook_message(sender_id, clean_reply)

                                            history = get_conversation(sender_id)
                                            summary = build_lead_summary(sender_id, history)
                                            send_sms_alert(summary)
                                            send_email_alert(summary)
    else:
                        send_facebook_message(sender_id, reply)

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port)
