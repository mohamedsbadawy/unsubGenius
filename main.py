from flask import Flask, render_template, request, jsonify
import threading
import imaplib
import email
from bs4 import BeautifulSoup
import re
import requests

# Flask application setup
app = Flask(__name__)

# Background task management
task_thread = None
task_status = {
    "running": False,
    "progress": "",
    "progress_percent": 0,
    "results": [],
    "completed_unsubscriptions": 0,
}

# List of accepted service providers and respective IMAP links
servers = [
    ('Gmail', 'imap.gmail.com'),
    ('Outlook', 'imap-mail.outlook.com'),
    ('Hotmail', 'imap-mail.outlook.com'),
    ('Yahoo', 'imap.mail.yahoo.com'),
    ('ATT', 'imap.mail.att.net'),
    ('Comcast', 'imap.comcast.net'),
    ('Verizon', 'incoming.verizon.net'),
    ('AOL', 'imap.aol.com'),
    ('Zoho', 'imap.zoho.com')
]

# Key words for unsubscribe link
words = ['unsubscribe', 'subscription', 'optout']


class AutoUnsubscriber:
    def __init__(self, email, password, server, delete_emails):
        self.email = email
        self.password = password
        self.server = server
        self.imap = None
        self.senderList = []  # [sender_name, sender_email, unsubscribe_url, spam_score]
        self.noLinkList = []
        self.wordCheck = [re.compile(word, re.I) for word in words]
        self.spam_threshold = 5
        self.delete_emails = delete_emails

    def login(self):
        """
        Log in to the IMAP server.
        """
        try:
            self.imap = imaplib.IMAP4_SSL(self.server)
            self.imap.login(self.email, self.password)
            self.imap.select('INBOX')
            task_status["progress"] = "Login successful"
            return True
        except imaplib.IMAP4.error as e:
            task_status["progress"] = f"Login error: {e}"
            return False

    def calculate_spam_score(self, sender_email):
        """
        Calculate a spam score based on the frequency of emails from a sender.
        """
        try:
            status, data = self.imap.search(None, f'FROM "{sender_email}"')
            if status != 'OK':
                return 0
            return len(data[0].split())
        except Exception as e:
            task_status["progress"] = f"Error calculating spam score for {sender_email}: {e}"
            return 0

    def getEmails(self):
        """
        Fetch emails and extract unsubscribe links. Calculate spam scores for senders.
        """
        try:
            task_status["progress"] = "Finding emails with unsubscribe links..."
            task_status["progress_percent"] = 25
            status, data = self.imap.search(None, 'BODY "unsubscribe"')
            if status != 'OK':
                task_status["progress"] = "No emails found."
                return
            UIDs = data[0].split()
            total_emails = len(UIDs)
            for idx, UID in enumerate(UIDs, start=1):
                try:
                    status, msg_data = self.imap.fetch(UID, '(RFC822)')
                    if status != 'OK':
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    sender = email.utils.parseaddr(msg['From'])
                    sender_name = sender[0]
                    sender_email = sender[1]
                    if sender_email in [s[1] for s in self.senderList]:
                        continue
                    url = None
                    for part in msg.walk():
                        if part.get_content_type() == "text/html":
                            html_content = part.get_payload(decode=True).decode('utf-8', errors='replace')
                            soup = BeautifulSoup(html_content, 'html.parser')
                            elems = soup.find_all('a', href=True)
                            for elem in elems:
                                if any(word.search(str(elem)) for word in self.wordCheck):
                                    url = elem.get('href')
                                    break
                    spam_score = self.calculate_spam_score(sender_email)
                    if url:
                        self.senderList.append([sender_name, sender_email, url, spam_score])
                        task_status["results"].append(
                            f"Found unsubscribe link from {sender_name} (Spam Score: {spam_score})"
                        )
                    else:
                        self.noLinkList.append([sender_name, sender_email])
                except Exception as e:
                    task_status["progress"] = f"Error processing email ID {UID}: {e}"
                task_status["progress_percent"] = int(25 + (50 * idx / total_emails))  # Update progress bar
        except Exception as e:
            task_status["progress"] = f"Error accessing emails: {e}"

    def auto_unsubscribe(self):
        """
        Automatically unsubscribe from flagged senders using requests.
        """
        task_status["progress"] = "Unsubscribing from senders..."
        task_status["progress_percent"] = 75
        completed_unsubscriptions = 0
        for sender in self.senderList:
            sender_name, sender_email, url, spam_score = sender
            if spam_score >= self.spam_threshold:
                try:
                    response = requests.get(url, timeout=10)
                    if response.status_code == 200:
                        completed_unsubscriptions += 1
                        task_status["results"].append(
                            f"Successfully unsubscribed from {sender_name}"
                        )
                        if self.delete_emails:
                            self.delete_email(sender_email)
                    else:
                        task_status["results"].append(
                            f"Failed to unsubscribe from {sender_name}, status code: {response.status_code}"
                        )
                except Exception as e:
                    task_status["results"].append(
                        f"Error unsubscribing from {sender_name}: {e}"
                    )
        task_status["completed_unsubscriptions"] = completed_unsubscriptions
        task_status["progress_percent"] = 100

    def delete_email(self, sender_email):
        """
        Delete emails from a given sender.
        """
        try:
            status, data = self.imap.search(None, f'FROM "{sender_email}"')
            if status == 'OK':
                for num in data[0].split():
                    self.imap.store(num, '+FLAGS', '\\Deleted')
                self.imap.expunge()
                task_status["results"].append(f"Deleted emails from {sender_email}")
        except Exception as e:
            task_status["results"].append(f"Error deleting emails from {sender_email}: {e}")

    def fullProcess(self):
        """
        Full process to fetch emails, calculate spam scores, and auto-unsubscribe.
        """
        if not self.login():
            return
        self.getEmails()
        self.auto_unsubscribe()


def run_background_task(email, password, server, delete_emails):
    """
    Run the email processing in the background.
    """
    task_status["running"] = True
    task_status["progress"] = "Starting..."
    task_status["progress_percent"] = 0
    task_status["results"] = []
    task_status["completed_unsubscriptions"] = 0
    auto_unsubscriber = AutoUnsubscriber(email, password, server, delete_emails)
    auto_unsubscriber.fullProcess()
    task_status["progress"] = "Completed"
    task_status["running"] = False


@app.route('/')
def index():
    return render_template('index.html', task_status=task_status)
@app.route('/unsub/howto')
def howto():
    return render_template('howto.html')

@app.route('/unsub/start', methods=['POST'])
def start_task():
    global task_thread
    if task_status["running"]:
        return jsonify({"status": "Task already running"}), 400

    email = request.form.get('email')
    password = request.form.get('password')
    provider = request.form.get('provider')
    delete_emails = request.form.get('delete_emails') == "on"
    server = next((s[1] for s in servers if s[0].lower() == provider.lower()), None)

    if not server:
        return jsonify({"status": "Invalid email provider"}), 400

    # Reset task status
    task_status["running"] = True
    task_status["progress"] = "Starting..."
    task_status["progress_percent"] = 0
    task_status["results"] = []
    task_status["completed_unsubscriptions"] = 0

    # Start the background task
    task_thread = threading.Thread(target=run_background_task, args=(email, password, server, delete_emails))
    task_thread.start()

    return jsonify({"status": "Task started"})


@app.route('/unsub/status', methods=['GET'])
def get_status():
    return jsonify(task_status)


if __name__ == '__main__':
    app.run(debug=False)
