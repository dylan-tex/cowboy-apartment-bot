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
early_leads_sent = set()  # Track which sender_ids have had early alerts sent
completed_leads = set()  # Track which sender_ids have completed their lead (all 9 pieces collected)

SYSTEM_PROMPT = """You are Melissa, a friendly and warm apartment locator assistant for Cowboy Apartment Locators. You are an AI chat tool. Your personality is inviting, upbeat, and customer-service focused.

PHASE 1 - LEAD COLLECTION:
Collect this info one or two questions at a time:
1. Their full name
2. Their email
3. Their phone number
4. Type of place (apartment, house, condo, etc.)
5. Number of bedrooms
6. Number of bathrooms
7. Preferred location/area (city/area they're moving to)
8. Monthly budget
9. Move-in date
10. Credit situation (excellent, good, fair, poor)
11. When they are available to tour

Start by warmly greeting the customer.

When you have collected their FULL NAME, EMAIL, PHONE NUMBER, and CITY/LOCATION, acknowledge this in your response. Say something like: "Perfect! I'm passing your contact info and location along to our team right now so they can start helping you find the perfect place. I'm an AI assistant here in this chat, so while they review your info, let me gather a few more details..."

Once you have ALL 9 pieces of information, end your final message with exactly:
LEAD_COMPLETE

PHASE 2 - CONSULTATION (After LEAD_COMPLETE):
Once all 9 pieces are collected, STOP asking for information. Instead:
- Answer questions about apartments, neighborhoods, the process, etc. based on what you know about their preferences
- Be helpful and supportive about their move
- If they ask something you don't have information about (like specific property availability, pricing, etc.), say: "I'm an AI chat assistant, so I don't have access to specific property listings or pricing, but our team will have all of that and will reach out to you soon with options based on your preferences!"
- Do NOT try to collect any additional information
- Do NOT restart the lead collection process
- Keep the conversation natural and helpful"""


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
        emails = ["dylansilver.tx@gmail.com", "matt@mattknowstexas.com"]
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


def send_alerts_background(summary):
    send_sms_alert(summary)
    send_email_alert(summary)


def extract_early_lead_info(messages):
    """Extract the 4 required pieces for early alert: full name, email, phone, city"""
    try:
        messages_text = ""
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            messages_text += role + ": " + content + "\n"
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": """Extract ONLY these 4 pieces from the conversation if present:
1. Full name (first and last)
2. Email address
3. Phone number
4. City or location they're moving to

Return in this exact format:
Name: [name or MISSING]
Email: [email or MISSING]
Phone: [phone or MISSING]
City: [city or MISSING]

Conversation:
""" + messages_text
            }]
        )
        return response.content[0].text
    except Exception as e:
        print("Extract error:", e)
        return "Name: MISSING\nEmail: MISSING\nPhone: MISSING\nCity: MISSING"


def check_early_lead_complete(extraction):
    """Check if all 4 required pieces are present"""
    has_name = "Name: MISSING" not in extraction and "Name:" in extraction
    has_email = "Email: MISSING" not in extraction and "Email:" in extraction
    has_phone = "Phone: MISSING" not in extraction and "Phone:" in extraction
    has_city = "City: MISSING" not in extraction and "City:" in extraction
    return has_name and has_email and has_phone and has_city


def send_early_lead_email(extraction):
    """Send email with the 4 key pieces of information"""
    try:
        sg = sendgrid.SendGridAPIClient(api_key=os.environ["SENDGRID_API_KEY"])
        emails = ["dylansilver.tx@gmail.com", "matt@mattknowstexas.com"]

        subject = "🔥 NEW EARLY LEAD - Cowboy Apartment Locators"
        body = f"""EARLY LEAD ALERT - Key Information Collected:

{extraction}

More details being collected...
"""

        for recipient in emails:
            message = Mail(
                from_email=os.environ.get("EMAIL_ADDRESS", "advertising@dylansilver.org"),
                to_emails=recipient,
                subject=subject,
                plain_text_content=body
            )
            sg.send(message)
    except Exception as e:
        print("Early lead email error:", e)


def build_lead_summary(messages):
    try:
        messages_text = ""
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            messages_text += role + ": " + content + "\n"
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5",
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
            model="claude-haiku-4-5",
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

                # Check for early lead (name + email + phone + city)
                if sender_id not in early_leads_sent:
                    extraction = extract_early_lead_info(conversation)
                    if check_early_lead_complete(extraction):
                        early_leads_sent.add(sender_id)
                        send_early_lead_email(extraction)
                        print(f"Early lead sent for {sender_id}")

                # Check for complete lead (all 9 pieces)
                if "LEAD_COMPLETE" in assistant_response:
                    clean_response = assistant_response.replace("LEAD_COMPLETE", "").strip()
                    send_facebook_message(sender_id, clean_response)
                    summary = build_lead_summary(conversation)
                    thread = threading.Thread(target=send_alerts_background, args=(summary,))
                    thread.daemon = True
                    thread.start()
                    # Mark as completed lead but KEEP the conversation (don't delete)
                    completed_leads.add(sender_id)
                    if sender_id in early_leads_sent:
                        early_leads_sent.remove(sender_id)
                else:
                    send_facebook_message(sender_id, assistant_response)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print("Webhook error:", e)
        return jsonify({"status": "error"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
