import os
import json
import random
import datetime
import email
import imaplib
import smtplib
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import feedparser
import pytz
from google import genai

# Configuration
GMAIL_ADDRESS = (os.environ.get("GMAIL_ADDRESS") or "rhlee.personal@gmail.com").strip()
GMAIL_APP_PASSWORD = (os.environ.get("GMAIL_APP_PASSWORD") or "").strip()
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
STATE_FILE = "state.json"

# Preferred Gemini models in order of attempt
MODELS_TO_TRY = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]

# Fallback message when AI models are temporarily unavailable
FALLBACK_AI_UNAVAILABLE_MESSAGE = (
    "AI unavailable at the moment, if you would like us to try again, "
    "please reply to this email and keep your original content at the bottom."
)

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

    clean_pass = GMAIL_APP_PASSWORD.replace(" ", "")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, clean_pass)
        server.send_message(msg)
    print(f"Successfully sent email to {to_email}")

def fetch_unread_emails() -> list:
    if not GMAIL_APP_PASSWORD:
        print("GMAIL_APP_PASSWORD not set. Skipping inbox fetch.")
        return []

    messages = []
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        clean_user = GMAIL_ADDRESS.strip()
        clean_pass = GMAIL_APP_PASSWORD.replace(" ", "").strip()
        print(f"Logging into Gmail as {clean_user}...")
        mail.login(clean_user, clean_pass)

        # Standard IMAP requires uppercase 'INBOX' and checking status
        select_status, select_data = mail.select("INBOX")
        if select_status != 'OK':
            print(f"Failed to select INBOX. Status: {select_status}, Data: {select_data}")
            mail.logout()
            return []

        # IMAP search specifically for UNSEEN emails with "mindsnack" in the subject
        search_status, response = mail.search(None, 'UNSEEN', 'SUBJECT', 'mindsnack')
        if search_status != 'OK':
            print(f"Failed to search INBOX. Status: {search_status}")
            mail.logout()
            return []

        email_ids = response[0].split()

        # If no emails found with 'mindsnack', also check for 'mindsnacks'
        if not email_ids:
            search_status_plural, response_plural = mail.search(None, 'UNSEEN', 'SUBJECT', 'mindsnacks')
            if search_status_plural == 'OK':
                email_ids = response_plural[0].split()

        # Take only the 50 most recent matching unread emails
        email_ids = email_ids[-50:]
        print(f"Found {len(email_ids)} matching MindSnack unread email(s).")

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
def generate_content_with_fallback(prompt: str) -> str:
    """Helper to generate content with fallback models. If all AI models fail or are unavailable,
    returns a polite fallback message prompting the user to reply to try again."""
    if not ai_client:
        print("Gemini AI client not initialized. Returning fallback message.")
        return FALLBACK_AI_UNAVAILABLE_MESSAGE

    for m in MODELS_TO_TRY:
        try:
            response = ai_client.models.generate_content(model=m, contents=prompt)
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            print(f"Model {m} encountered error: {e}. Trying fallback if available...")

    print("All Gemini AI models failed or were unavailable. Returning fallback message.")
    return FALLBACK_AI_UNAVAILABLE_MESSAGE

def clean_html(raw_html: str) -> str:
    """Strips HTML tags and compresses whitespace from RSS feed text."""
    clean_text = re.sub(r'<[^>]+>', ' ', raw_html)
    return ' '.join(clean_text.split())

def fetch_podcast_context() -> str:
    snippets = []
    for pod in DEFAULT_PODCASTS:
        try:
            feed = feedparser.parse(pod["rss"])
            if feed.entries:
                # Grab the 2 most recent episodes for richer depth
                for entry in feed.entries[:2]:
                    title = getattr(entry, 'title', 'Untitled Episode')
                    summary_raw = getattr(entry, 'summary', getattr(entry, 'description', ''))
                    clean_sum = clean_html(summary_raw)[:800]
                    snippets.append(
                        f"Podcast: {pod['name']}\n"
                        f"Episode: {title}\n"
                        f"Summary: {clean_sum}"
                    )
        except Exception as e:
            print(f"Error reading feed {pod['name']}: {e}")
    return "\n\n---\n\n".join(snippets)

def generate_welcome_and_first_mindsnack(user_prompt: str) -> str:
    context = fetch_podcast_context()
    prompt = f"""
    You are writing the welcome email for MindSnacks & MailTreats.
    User's signup prompt / interests:
    "{user_prompt}"

    Latest Podcast Context (Real Episode Data from Hidden Brain, Solved, Perform):
    {context}

    CRITICAL INSTRUCTION:
    You MUST directly dig into and feature specific ideas from the Podcast Context above.
    Do NOT give a generic high-level summary or restate the user's preferences back to them.

    Email Structure:
    1. Warm Welcome: 1-2 friendly sentences thanking them for joining.
    2. MindSnack Deep Dive: Pick AT LEAST ONE specific podcast and episode title from the Podcast Context above. Extract a specific, concrete psychological insight, neuroscience finding, or health concept from that episode. Explain the concept clearly in 2-3 sentences.
    3. Actionable Health Nudge: Give them 1 specific, immediate 2-minute action (e.g., a specific breathing exercise, a quick posture reset, walking away from screens for a 3-minute stretch, or a hydration prompt).
    4. MailTreat Call-to-Action: Remind them that replying to this email keeps the AI conversation going and qualifies them for this month's physical MailTreat reward!

    Tone & Formatting Constraints:
    - Friendly, engaging, and clear.
    - Do NOT use bold markdown formatting (no **text**).
    """
    return generate_content_with_fallback(prompt)

def generate_reply_conversation(user_message: str, user_prompt: str) -> str:
    context = fetch_podcast_context()
    prompt = f"""
    You are continuing an ongoing MindSnacks conversation with a user.
    User starting preferences: "{user_prompt}"
    User latest email reply: "{user_message}"

    Podcast Context:
    {context}

    Task:
    Respond thoughtfully and conversationally. Reference a concrete idea from the podcasts (Hidden Brain, Solved, or Perform) or build directly on the user's message with a specific psychology/wellness takeaway and an actionable health nudge (rest, walk, stretch, or mind break).

    Constraints:
    - Conversational, brief, and supportive.
    - Do NOT use bold markdown formatting.
    """
    return generate_content_with_fallback(prompt)

def generate_weekly_mindsnack(user_prompt: str) -> str:
    context = fetch_podcast_context()
    prompt = f"""
    Write a fresh weekly MindSnack email.
    User interests: "{user_prompt}"

    Podcast Context:
    {context}

    Task:
    1. Feature a specific episode and key takeaway from the Podcast Context (Hidden Brain, Solved with Mark Manson, or Perform with Andy Galpin).
    2. Provide a quick, actionable health/wellness nudge for today (rest, walk, hydration, or movement).

    Constraints:
    - Concise and ready for email dispatch.
    - Do NOT use bold markdown formatting.
    """
    return generate_content_with_fallback(prompt)

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
        body = email_msg["body"]

        # Ignore emails sent by the app itself
        if sender == GMAIL_ADDRESS.lower():
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
