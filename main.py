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

# Fallback message when AI models are temporarily unavailable
FALLBACK_AI_UNAVAILABLE_MESSAGE = (
    "AI unavailable at the moment, if you would like us to try again, "
    "please reply to this email and keep your original content at the bottom."
)

# Default podcasts to pull content from
DEFAULT_PODCASTS = [
    {"name": "Hidden Brain", "rss": "https://feeds.simplecast.com/829z8U31"},
    {"name": "Solved with Mark Manson", "rss": "https://feeds.buzzsprout.com/2192131.rss"},
    {"name": "Perform with Andy Galpin", "rss": "https://feeds.megaphone.fm/perform"},
    {"name": "Huberman Lab", "rss": "https://feeds.megaphone.fm/hubermanlab"}
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

        # Take only the 10 most recent matching unread emails
        email_ids = email_ids[-10:]
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
# Gemini AI Generation Logic & Context Helpers
# -------------------------------------------------------------------
def clean_html(raw_html: str) -> str:
    clean = re.sub(r'<[^>]+>', '', raw_html)
    return " ".join(clean.split())

def fetch_podcast_context() -> str:
    snippets = []
    for pod in DEFAULT_PODCASTS:
        try:
            feed = feedparser.parse(pod["rss"])
            if feed.entries:
                for entry in feed.entries[:2]:
                    clean_summary = clean_html(entry.get("summary", ""))[:800]
                    snippets.append(
                        f"Podcast: {pod['name']}\n"
                        f"Episode: {entry.get('title', 'Latest Episode')}\n"
                        f"Summary: {clean_summary}"
                    )
        except Exception as e:
            print(f"Error reading feed {pod['name']}: {e}")
    return "\n\n---\n\n".join(snippets)

def generate_content_with_fallback(prompt: str) -> str:
    """Helper to generate content with fallback models. If all AI models fail or are unavailable,
    returns a polite fallback message prompting the user to reply to try again."""
    if not ai_client:
        print("Gemini AI client not initialized. Returning fallback message.")
        return FALLBACK_AI_UNAVAILABLE_MESSAGE

    models_to_try = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
    for m in models_to_try:
        try:
            response = ai_client.models.generate_content(model=m, contents=prompt)
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            print(f"Model {m} encountered error: {e}. Trying fallback if available...")

    print("All Gemini AI models failed or were unavailable. Returning fallback message.")
    return FALLBACK_AI_UNAVAILABLE_MESSAGE

def generate_welcome_and_first_mindsnack(user_prompt: str) -> str:
    context = fetch_podcast_context()
    prompt = f"""
    The user just signed up for MindSnacks & MailTreats with this prompt/preferences:
    "{user_prompt}"

    Podcast feed updates context:
    {context}

    Task:
    Write an email response that:
    1. Warmly thanks them for signing up and explains that we are replying immediately with their first MindSnack, and future ones will arrive about once a week.
    2. Delivers their very first MindSnack:
       - Check the user's prompt carefully for any specific podcasts, authors, or topics they requested (e.g., Hidden Brain, Solved, Huberman Lab, Perform, neuroscience, psychology, etc.).
       - If they requested a podcast or topic present in the RSS feed context above, prioritize selecting an episode from that feed.
       - FALLBACK RULE: If the user requested a podcast, news source, book, or topic NOT present in the RSS feed context above, draw upon your broad general knowledge of that requested show/subject to deliver a relevant, accurate insight and health nudge.
       - Name the podcast or topic explicitly, share a concrete psychological, neuroscience, or wellness insight from it, and provide an actionable health nudge (e.g., 2-minute movement snack, stretch, hydration, or mindfulness exercise).
    3. Mentions that replying to this email keeps the AI conversation going and qualifies them for this month's physical MailTreat reward!

    Strict rules:
    - Do NOT repeat or echo exact phrases, greetings, or sign-offs from the user's message.
    - Keep tone warm, concise, and engaging. Do not use bold markdown formatting.
    """
    return generate_content_with_fallback(prompt)

def generate_reply_conversation(user_message: str, user_prompt: str) -> str:
    context = fetch_podcast_context()
    prompt = f"""
    User starting preferences: "{user_prompt}"
    User latest reply message: "{user_message}"

    Podcast updates context:
    {context}

    Task:
    Respond thoughtfully to the user's latest message as Gemini AI.
    1. Check what podcasts or topics the user requested in their starting preferences or latest message.
    2. If their preferred podcast/topic is in the podcast RSS context above, draw from that context.
    3. FALLBACK RULE: If they asked about a show, news source, or topic NOT present in the RSS context above, draw from your broad general knowledge to answer thoughtfully and accurately.
    4. Provide an engaging follow-up insight or health tip based on what they said and an actionable wellness nudge.

    Strict rules:
    - Do NOT repeat or echo exact phrases, sentence structures, or sign-offs from the user's previous emails or prompt.
    - Keep sign-offs fresh, natural, and unique to this specific message.
    - Keep tone conversational, supportive, and concise. Do not use bold markdown formatting.
    """
    return generate_content_with_fallback(prompt)

def generate_weekly_mindsnack(user_prompt: str) -> str:
    context = fetch_podcast_context()
    prompt = f"""
    User prompt/interests: "{user_prompt}"
    Podcast updates:
    {context}

    Task:
    Write a fresh weekly MindSnack email. Include:
    1. Check user preferences and pick an episode from their preferred podcasts in the context. If their preferred topic/show is not in the context, draw from your broad general knowledge of their requested topics.
    2. An interesting psychology, neuroscience, or wellness tidbit based on that topic/show.
    3. A quick, actionable health nudge for today.
    
    Strict rules:
    - Keep sign-offs fresh and avoid repetitive canned phrases.
    - Do not use bold markdown formatting.
    """
    return generate_content_with_fallback(prompt)

def format_email_with_quoted_original(response_text: str, original_message: str) -> str:
    """Appends the original incoming user email at the bottom of the response."""
    if not original_message.strip():
        return response_text
    
    return f"{response_text}\n\n----------------------------------------\nOriginal Message:\n{original_message}"

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
            ai_reply = generate_welcome_and_first_mindsnack(body)
            full_email_body = format_email_with_quoted_original(ai_reply, body)
            send_email(
                to_email=sender,
                subject="Welcome to MindSnacks & MailTreats! Here is your 1st MindSnack",
                body_text=full_email_body
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
            ai_reply = generate_reply_conversation(body, users[sender]["raw_prompt"])
            full_email_body = format_email_with_quoted_original(ai_reply, body)
            send_email(
                to_email=sender,
                subject="Re: Your MindSnack Conversation",
                body_text=full_email_body
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
