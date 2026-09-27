import os
import json
import random
import datetime
import email
import imaplib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import feedparser
import pytz
from google import genai

# Configuration
#GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "rhlee.personal@gmail.com")
#GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "rhlee.personal@gmail.com").strip()
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
STATE_FILE = "state.json"

# Default podcasts to pull content from
DEFAULT_PODCASTS = [
    {"name": "Hidden Brain", "rss": "https://feeds.simplecast.com/829z8U31"},
    {"name": "Solved with Mark Manson", "rss": "https://feeds.buzzsprout.com/2192131.rss"},
    {"name": "Perform with Andy Galpin", "rss": "https://feeds.megaphone.fm/perform"}
]

# Initialize Gemini Client
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# -------------------------------------------------------------------
# State Management
# -------------------------------------------------------------------
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"users": {}}

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# -------------------------------------------------------------------
# Email Helpers (IMAP & SMTP)
# -------------------------------------------------------------------
def send_email(to_email: str, subject: str, body_text: str):
    if not GMAIL_APP_PASSWORD:
        print(f"[DRY RUN] Would send email to {to_email} with subject: {subject}")
        return

    msg = MIMEMultipart()
    msg['From'] = f"MindSnacks & MailTreats <{GMAIL_ADDRESS}>"
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body_text, 'plain'))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)
    print(f"Successfully sent email to {to_email}")

def fetch_unread_emails() -> list:
    if not GMAIL_APP_PASSWORD: print("GMAIL_APP_PASSWORD is empty or not set. Skipping inbox fetch.")
    return []

    messages = []
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        clean_user = GMAIL_ADDRESS.strip()
        clean_pass = GMAIL_APP_PASSWORD.replace(" ", "").strip()
        print(f"Logging into Gmail as {clean_user} (Password length: {len(clean_pass)} chars)...")
        mail.login(clean_user, clean_pass)
        
#def fetch_unread_emails() -> list:
#    if not GMAIL_APP_PASSWORD:
#        print("GMAIL_APP_PASSWORD not set. Skipping inbox fetch.")
#        return []
#
#    messages = []
#    try:
#        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        print(f"Attempting login for '{GMAIL_ADDRESS}' with password length: {len(GMAIL_APP_PASSWORD or '')}")
#        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
#        mail.select("inbox")

        status, response = mail.search(None, 'UNSEEN')
        email_ids = response[0].split()

        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, '(RFC822)')
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    sender = email.utils.parseaddr(msg.get("From"))[1]
                    subject = msg.get("Subject", "")
                    
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode(errors='ignore')
                                break
                    else:
                        body = msg.get_payload(decode=True).decode(errors='ignore')

                    messages.append({
                        "sender": sender.lower().strip(),
                        "subject": subject,
                        "body": body.strip()
                    })
        mail.logout()
    except Exception as e:
        print(f"Error fetching emails: {e}")

    return messages

# -------------------------------------------------------------------
# Gemini AI Generation Logic
# -------------------------------------------------------------------
def fetch_podcast_context() -> str:
    snippets = []
    for pod in DEFAULT_PODCASTS:
        try:
            feed = feedparser.parse(pod["rss"])
            if feed.entries:
                latest = feed.entries[0]
                snippets.append(f"Podcast: {pod['name']}\nEpisode: {latest.title}\nSummary: {latest.summary[:400]}")
        except Exception as e:
            print(f"Error reading feed {pod['name']}: {e}")
    return "\n\n---\n\n".join(snippets)

def generate_welcome_and_first_mindsnack(user_prompt: str) -> str:
    context = fetch_podcast_context()
    prompt = f"""
    The user just signed up for MindSnacks & MailTreats with this prompt:
    "{user_prompt}"

    Podcast updates context:
    {context}

    Task:
    Write an email response that:
    1. Warmly thanks them for signing up and explains that we are replying immediately with their first MindSnack, and future ones will arrive about once a week.
    2. Delivers their very first MindSnack: (a) a fascinating tidbit based on their interests or recent podcast topics, and (b) an actionable health nudge (like a 2-minute workout snack, hydration, or gratitude).
    3. Mentions that replying to this email keeps the AI conversation going and qualifies them for this month's physical MailTreat reward!

    Keep tone friendly, concise, and do not use bold markdown formatting.
    """
    response = ai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    return response.text.strip()

def generate_reply_conversation(user_message: str, user_prompt: str) -> str:
    prompt = f"""
    User starting preferences: "{user_prompt}"
    User latest reply: "{user_message}"

    Task:
    Respond thoughtfully to the user's message as Gemini AI. Provide an engaging follow-up insight or health tip based on what they said. Keep it conversational, brief, and supportive. Do not use bold markdown formatting.
    """
    response = ai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    return response.text.strip()

def generate_weekly_mindsnack(user_prompt: str) -> str:
    context = fetch_podcast_context()
    prompt = f"""
    User prompt/interests: "{user_prompt}"
    Podcast updates:
    {context}

    Task:
    Write a fresh weekly MindSnack email. Include:
    1. An interesting psychology, neuroscience, or wellness tidbit derived from their interests/podcasts.
    2. A quick, actionable health nudge for today.
    
    Keep it concise and ready for email. Do not use bold markdown formatting.
    """
    response = ai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    return response.text.strip()

# -------------------------------------------------------------------
# Main Workflow Execution
# -------------------------------------------------------------------
def main():
    state = load_state()
    users = state.setdefault("users", {})
    now_iso = datetime.datetime.now(pytz.utc).isoformat()

    # Step 1: Process incoming unseen emails
    unread_emails = fetch_unread_emails()
    for email_msg in unread_emails:
        sender = email_msg["sender"]
        subject = email_msg["subject"]
        body = email_msg["body"]

        # Ignore emails sent by the app itself
        if sender == GMAIL_ADDRESS.lower():
            continue

        # Filter: Only process emails where the subject line contains "mindsnack" or "mindsnacks" (case-insensitive)
        if "mindsnack" not in subject.lower():
            print(f"Skipping email from {sender} — subject '{subject}' does not contain 'mindsnack'")
            continue

        if sender not in users:
            # NEW USER SIGNUP
            print(f"New user signup from: {sender}")
            welcome_msg = generate_welcome_and_first_mindsnack(body)
            send_email(
                to_email=sender,
                subject="Welcome to MindSnacks & MailTreats! Here is your 1st MindSnack",
                body_text=welcome_msg
            )
            users[sender] = {
                "raw_prompt": body,
                "joined_at": now_iso,
                "last_sent_at": now_iso,
                "replied_this_month": True
            }
        else:
            # EXISTING USER REPLY
            print(f"Received conversation reply from: {sender}")
            reply_msg = generate_reply_conversation(body, users[sender]["raw_prompt"])
            send_email(
                to_email=sender,
                subject="Re: Your MindSnack Conversation",
                body_text=reply_msg
            )
            users[sender]["replied_this_month"] = True

    # Step 2: Check existing users for weekly randomized dispatch
    for sender, data in users.items():
        last_sent = datetime.datetime.fromisoformat(data["last_sent_at"])
        days_since_last = (datetime.datetime.now(pytz.utc) - last_sent).days

        if days_since_last >= 5 and random.random() < 0.2:
            print(f"Triggering weekly MindSnack for: {sender}")
            weekly_msg = generate_weekly_mindsnack(data["raw_prompt"])
            send_email(
                to_email=sender,
                subject="Your Weekly MindSnack is Here!",
                body_text=weekly_msg
            )
            data["last_sent_at"] = now_iso

    save_state(state)
    print("Execution completed.")

if __name__ == "__main__":
    main()
