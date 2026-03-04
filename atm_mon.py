"""
ATM Cash Balance Monitor — Switch Commerce TMS
===============================================
Monitors your TMS portal and alerts you on EVERY balance change under $2,000.

Setup (one-time):
  bash setup.sh
"""

import socket
import schedule
import time
import smtplib
import json
import os
import re
import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# Config — edit .env to change these
# ─────────────────────────────────────────────

ATM_LOGIN_URL          = os.getenv("ATM_LOGIN_URL", "https://www.switchcommerce.net/TMS/Login.aspx")
ATM_USERNAME           = os.getenv("ATM_USERNAME")
ATM_PASSWORD           = os.getenv("ATM_PASSWORD")
ATM_TERMINAL_URL       = os.getenv("ATM_TERMINAL_URL")
ALERT_THRESHOLD        = int(os.getenv("ALERT_THRESHOLD", 2000))
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", 5))
SMTP_EMAIL             = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD          = os.getenv("SMTP_PASSWORD")
ALERT_TO               = os.getenv("ALERT_TO")

_missing = [k for k, v in {
    "ATM_USERNAME": ATM_USERNAME, "ATM_PASSWORD": ATM_PASSWORD,
    "ATM_TERMINAL_URL": ATM_TERMINAL_URL, "SMTP_EMAIL": SMTP_EMAIL,
    "SMTP_PASSWORD": SMTP_PASSWORD, "ALERT_TO": ALERT_TO,
}.items() if not v]
if _missing:
    raise SystemExit(f"Missing required .env values: {', '.join(_missing)}\nCopy .env.example to .env and fill them in.")

# ─────────────────────────────────────────────
# Core Logic
# ─────────────────────────────────────────────

STATE_FILE = "atm_state.json"
last_known_balance = None

# Persistent session — reuses cookies so we only log in when the session expires
_session = None


def get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    return _session


class LoginIssue(Exception):
    """Raised when TMS has a login/auth problem that needs human attention."""
    def __init__(self, code, human_message):
        self.code = code
        self.human_message = human_message
        super().__init__(human_message)


def load_state():
    global last_known_balance
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            data = json.load(f)
            last_known_balance = data.get("last_balance")
            print(f"[Resume] Last known balance: ${last_known_balance}")


def save_state(balance):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_balance": balance}, f)


def send_alert(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = SMTP_EMAIL
        msg["To"]      = ALERT_TO
        with smtplib.SMTP_SSL("smtp.mail.yahoo.com", 465) as server:
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, ALERT_TO, msg.as_string())
        print(f"  [Alert Sent] {subject}")
    except smtplib.SMTPAuthenticationError:
        print(
            "  [Alert FAILED] Yahoo rejected the login.\n"
            "  Your App Password may be expired or wrong.\n"
            "  Fix: Yahoo Account → Security → App Passwords → generate a new one\n"
            "  Then update SMTP_PASSWORD in .env."
        )
    except smtplib.SMTPRecipientsRefused as e:
        print(
            f"  [Alert FAILED] Yahoo refused the recipient address: {ALERT_TO}\n"
            f"  Detail: {e}\n"
            f"  Yahoo sometimes blocks emails to SMS gateway addresses (@tmomail.net etc).\n"
            f"  Try a real email address for ALERT_TO instead."
        )
    except smtplib.SMTPSenderRefused as e:
        print(
            f"  [Alert FAILED] Yahoo refused the sender address: {SMTP_EMAIL}\n"
            f"  Detail: {e}"
        )
    except smtplib.SMTPException as e:
        code = e.smtp_code if hasattr(e, "smtp_code") else "?"
        print(
            f"  [Alert FAILED] SMTP error {code}: {e}\n"
            f"  This is usually a server-side rejection (554 = delivery refused).\n"
            f"  Common causes: expired App Password, Yahoo spam filter, or blocked recipient.\n"
            f"  Check SMTP_EMAIL / SMTP_PASSWORD / ALERT_TO in .env."
        )
    except OSError as e:
        print(
            f"  [Alert FAILED] Network error: {e}\n"
            f"  Could not reach smtp.mail.yahoo.com — check your internet connection."
        )


def parse_balance(text):
    cleaned = re.sub(r"[^\d.]", "", text.strip())
    return float(cleaned) if cleaned else None


def _do_login(session):
    """Log in to TMS. Raises LoginIssue on any auth problem."""
    # Load login page — grab ASP.NET hidden fields (ViewState, EventValidation, etc.)
    resp = session.get(ATM_LOGIN_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    form = soup.find("form")
    payload = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        inp_type = inp.get("type", "text").lower()
        # Only send the Login button, not Reset/BtnDontReset/HiddenButton2
        if inp_type == "submit" and name != "ctl00$BodyContent$LoginButton":
            continue
        payload[name] = inp.get("value", "")

    # ASP.NET form names use $ instead of _
    payload["ctl00$BodyContent$UserName"] = ATM_USERNAME
    payload["ctl00$BodyContent$Password"] = ATM_PASSWORD

    resp = session.post(ATM_LOGIN_URL, data=payload, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Detect password expiration notice — auto-click "Remind me later" to proceed.
    # TMS shows this in a fresh session even if you've dismissed it in the browser.
    # BtnDontReset is an <input type="submit">, so we submit it as a normal button
    # (not via __EVENTTARGET which is only for LinkButtons).
    if soup.find(id="ctl00_BodyContent_BtnDontReset"):
        print("  [Auth] Password expiration notice — clicking 'Remind me later'...")
        form = soup.find("form")
        remind_payload = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            inp_type = inp.get("type", "text").lower()
            # Browsers only send the clicked submit button — exclude all others
            if inp_type == "submit" and name != "ctl00$BodyContent$BtnDontReset":
                continue
            remind_payload[name] = inp.get("value", "")
        resp = session.post(resp.url, data=remind_payload, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

    # Detect login failure
    for error_id in ["ctl00_BodyContent_InvalidLogin", "ctl00_BodyContent_ErrorLabel"]:
        el = soup.find(id=error_id)
        if el:
            err_text = el.get_text(strip=True)
            if err_text:
                raise LoginIssue(
                    "LOGIN_FAILED",
                    f"🚫 TMS login failed.\n"
                    f"  Error shown: \"{err_text}\"\n"
                    f"  Check your ATM_USERNAME / ATM_PASSWORD in .env.\n"
                    f"  Also check if your account is locked."
                )

    if "Login.aspx" in resp.url:
        with open("atm_login_debug.html", "w") as f:
            f.write(resp.text)
        raise LoginIssue(
            "LOGIN_STUCK",
            f"🚫 Still on the login page after submitting.\n"
            f"  This usually means wrong credentials or an unexpected prompt.\n"
            f"  HTML saved: atm_login_debug.html"
        )


def _parse_balance_from_page(soup):
    """Extract balance from cassettes grid. Returns balance text or None."""
    try:
        table = soup.find(id="ctl00_BodyContent_CassettesGridView")
        return table.find_all("tr")[1].find_all("td")[4].get_text(strip=True)
    except Exception:
        return None


def get_balance():
    global _session
    session = get_session()

    # ── 1. Try terminal page directly (reuses session if already logged in) ──
    resp = session.get(ATM_TERMINAL_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # If redirected to login, authenticate then retry
    if "Login.aspx" in resp.url:
        print("  [Auth] Session expired — logging in...")
        _do_login(session)
        resp = session.get(ATM_TERMINAL_URL, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

    # ── 2. Parse balance ───────────────────────────────────────────────────
    balance_text = _parse_balance_from_page(soup)
    if balance_text is None:
        with open("atm_balance_debug.html", "w") as f:
            f.write(resp.text)
        raise LoginIssue(
            "BALANCE_NOT_FOUND",
            f"⚠️  Logged in but couldn't find the balance on the terminal page.\n"
            f"  URL: {ATM_TERMINAL_URL}\n"
            f"  HTML saved: atm_balance_debug.html"
        )

    if not balance_text:
        raise ValueError("Balance element was empty.")

    balance = parse_balance(balance_text)
    if balance is None:
        raise ValueError(f"Could not parse a number from: '{balance_text}'")

    return balance


def check_balance():
    global last_known_balance
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] Checking balance...")

    try:
        balance = get_balance()
        print(f"  💵 Current balance: ${balance:,.2f}")

        if balance < ALERT_THRESHOLD:
            if last_known_balance is None or balance != last_known_balance:
                change_str = ""
                if last_known_balance is not None:
                    diff = balance - last_known_balance
                    direction = "▲ UP" if diff > 0 else "▼ DOWN"
                    change_str = f"  Change:  {direction} ${abs(diff):,.2f} (was ${last_known_balance:,.2f})\n"

                subject = f"⚠️ ATM Cash Alert: ${balance:,.2f}"
                body = (
                    f"ATM Cash Balance Alert\n"
                    f"{'─' * 32}\n"
                    f"  Balance: ${balance:,.2f}\n"
                    f"{change_str}"
                    f"  Time:    {now}\n"
                    f"  Status:  BELOW ${ALERT_THRESHOLD:,} threshold\n"
                    f"  From:    {socket.gethostname()}\n"
                )
                send_alert(subject, body)
            else:
                print("  ℹ️  Balance unchanged, no alert sent.")
        else:
            print(f"  ✅ Balance ${balance:,.2f} is above threshold, no alert.")

        last_known_balance = balance
        save_state(balance)

    except LoginIssue as e:
        # Stop all checks immediately to avoid locking the account
        print(f"\n  🚨 LOGIN ISSUE [{e.code}]\n  {e.human_message}\n")
        send_alert(
            f"🚨 ATM Monitor PAUSED — {e.code}",
            f"ATM Monitor — Action Required\n"
            f"{'─' * 32}\n"
            f"  Issue: {e.code}\n"
            f"  Time:  {now}\n\n"
            f"{e.human_message}\n\n"
            f"⛔ Checks are PAUSED to protect your account from lockout.\n"
            f"   Fix the issue, then restart the monitor script."
        )
        print("  ⛔ Pausing all checks. Restart the script after fixing.")
        schedule.clear()

    except Exception as e:
        print(f"  ❌ Unexpected error: {e}")
        send_alert(
            "⚠️ ATM Monitor ERROR",
            f"Unexpected error at {now}.\n\nError: {e}\n\nWill retry next interval."
        )


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("🏧 ATM Balance Monitor — Switch Commerce TMS")
    print(f"   Threshold  : Below ${ALERT_THRESHOLD:,}")
    print(f"   Interval   : Every {CHECK_INTERVAL_MINUTES} minutes")
    print(f"   Alerting   : {ALERT_TO}")
    print()

    load_state()
    check_balance()  # Run immediately on start

    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(check_balance)

    while True:
        schedule.run_pending()
        time.sleep(30)
