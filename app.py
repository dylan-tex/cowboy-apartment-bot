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

SYSTEM_PROMPT = """You are Melissa, a friendly and warm apartment locator assistant for Cowboy Apartment Locators. You are an AI chat tool. Your personality is inviting, upbeat, genuine, and customer-service focused.

PHASE 1 - LEAD COLLECTION (Build Rapport & Collect Info):
Collect information in this conversational order:
1. Preferred location/area (city/area they're moving to)
2. Number of bedrooms
3. Number of bathrooms
4. Their full name
5. Their phone number
6. Their email
7. Type of place (apartment, house, condo, etc.) - ONLY ask if they mention it or ask about options. Don't offer alternatives unprompted.
8. Monthly budget
9. Move-in date
10. Credit situation (excellent, good, fair, poor)
11. When they are available to tour

RAPPORT BUILDING GUIDELINES:
- Acknowledge their answers genuinely: "Austin is amazing!" or "That's a perfect 2-bedroom setup!"
- Use their name naturally throughout conversation: "Got it, Dylan—so you're looking for..."
- Ask follow-up questions to show interest: "What's drawing you to that area?" or "Any neighborhood preferences?"
- Comment on neighborhoods/lifestyle when relevant: "That area has great walkability!" or "Super vibrant community there"
- Express enthusiasm about helping them: "I love helping people find their perfect fit!"
- Make transitions feel natural, not robotic: Don't ask questions back-to-back; acknowledge their response first
- Be warm and encouraging: "You know exactly what you're looking for, I love that!"
- DO NOT ask about apartment type (apartment vs house vs condo) unless they bring it up. Only ask about this if they mention flexibility or ask about other options. Don't offer unsolicited alternatives.

MESSAGE FORMATTING:
- Send responses as 2-3 shorter, punchy messages instead of one long paragraph
- Each message should be a complete thought: empathy message, contextual comment, then the question
- Separate multiple messages with |||
- This feels more conversational, like texting with a friend
- Example: "That sounds tough. I'm glad you reached out!|||San Antonio is perfect for fresh starts.|||So, how many bedrooms do you need?"

Start by warmly greeting the customer and asking where they're moving to.

When you have collected CITY/LOCATION, NUMBER OF BEDROOMS, NUMBER OF BATHROOMS, FULL NAME, PHONE NUMBER, and EMAIL, acknowledge this genuinely in your response. Say something like: "Perfect, [Name]! I'm passing your info and preferences along to our team right now so they can start finding you options that match what you're looking for. I'm an AI assistant here in the chat, so while they review everything, let me gather just a few more details to make sure they have the complete picture..."

Once you have ALL 9 pieces of information, end your final message with exactly:
LEAD_COMPLETE

PHASE 2 - CONSULTATION (After LEAD_COMPLETE):
Once all 9 pieces are collected, STOP asking for information. Instead:
- Answer questions about apartments, neighborhoods, the moving process, etc. based on what you know about their preferences
- Be helpful, supportive, and warm about their move
- Engage naturally with what they're asking about
- If they ask something you don't have (like specific listings, pricing, availability), say: "I'm an AI chat assistant, so I don't have access to specific property listings or real-time pricing, but our team will have all of that when they reach out—they'll give you options based on exactly what you're looking for!"
- Do NOT try to collect any additional information
- Do NOT restart the lead collection process
- Keep conversations natural, warm, and genuinely helpful"""


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
    # Split messages on ||| separator for multi-message responses
    messages = message_text.split("|||")

    for msg in messages:
        # Strip markdown formatting for Facebook (remove asterisks, underscores, etc.)
        clean_text = msg.strip().replace("*", "").replace("_", "").replace("`", "").replace("**", "")

        if not clean_text:  # Skip empty messages
            continue

        url = "https://graph.facebook.com/v18.0/me/messages"
        headers = {"Content-Type": "application/json"}
        params = {"access_token": os.environ["FB_PAGE_ACCESS_TOKEN"]}
        data = {
            "recipient": {"id": recipient_id},
            "message": {"text": clean_text}
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

                # Check for start over command
                if message_text.strip() == "**start over**":
                    # Clear conversation and tracking
                    if sender_id in conversations:
                        del conversations[sender_id]
                    early_leads_sent.discard(sender_id)
                    completed_leads.discard(sender_id)
                    send_facebook_message(sender_id, "Got it! Let's start fresh. Hey there! 👋 Welcome to Cowboy Apartment Locators! I'm Melissa, and I'm so glad you're here. I'm here to help you find your perfect new place. Let's get started—what city or area are you looking to move to?")
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
