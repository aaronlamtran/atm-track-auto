"""
ATM Cash Balance Monitor — Switch Commerce TMS
===============================================
Monitors your TMS portal and alerts you on EVERY balance change under $2,000.

Setup (one-time):
  bash setup.sh

ChromeDriver is downloaded automatically via Selenium Manager (built into Selenium 4.6+).
"""

import sys
import schedule
import time
import smtplib
import json
import os
import re
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
            "  Then update SMTP_PASSWORD in this script."
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
        # Catches 554 and other SMTP-level errors (server-side rejection)
        code = e.smtp_code if hasattr(e, "smtp_code") else "?"
        print(
            f"  [Alert FAILED] SMTP error {code}: {e}\n"
            f"  This is usually a server-side rejection (554 = delivery refused).\n"
            f"  Common causes: expired App Password, Yahoo spam filter, or blocked recipient.\n"
            f"  Check SMTP_EMAIL / SMTP_PASSWORD / ALERT_TO in the script."
        )
    except OSError as e:
        print(
            f"  [Alert FAILED] Network error: {e}\n"
            f"  Could not reach smtp.mail.yahoo.com — check your internet connection."
        )


def parse_balance(text):
    cleaned = re.sub(r"[^\d.]", "", text.strip())
    return float(cleaned) if cleaned else None


def get_balance():
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    import shutil

    options = Options()
    if sys.platform == "linux":
        # Raspberry Pi headless: Chromium tries to init EGL (hardware GPU) which
        # requires an X display — even in headless mode. Fix:
        #   --ozone-platform=headless  → use Chromium's built-in headless display
        #   --use-gl=swiftshader       → software renderer, no X/EGL needed
        options.add_argument("--headless=new")
        options.add_argument("--ozone-platform=headless")
        options.add_argument("--use-gl=swiftshader")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
    # macOS: run with a visible browser (headless crashes on ARM Mac)
    options.add_argument("--window-size=1280,800")

    # On Raspberry Pi, chromedriver may not be in PATH — check common install locations.
    # On other platforms, Selenium Manager auto-downloads the right driver.
    CHROMEDRIVER_CANDIDATES = [
        shutil.which("chromedriver"),               # in PATH (some Pi OS versions)
        "/usr/lib/chromium-browser/chromedriver",   # Raspberry Pi OS (Bullseye/Bookworm)
        "/usr/bin/chromedriver",                    # Debian/Ubuntu
        "/usr/lib/chromium/chromedriver",           # some Debian variants
    ]
    CHROMIUM_CANDIDATES = [
        shutil.which("chromium-browser"),
        shutil.which("chromium"),
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]

    system_chromedriver = next((p for p in CHROMEDRIVER_CANDIDATES if p and os.path.exists(p)), None)
    system_chromium     = next((p for p in CHROMIUM_CANDIDATES     if p and os.path.exists(p)), None)

    if system_chromedriver:
        print(f"  [Driver] Using system chromedriver: {system_chromedriver}")
        service = Service(system_chromedriver)
        if system_chromium:
            options.binary_location = system_chromium
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)  # Selenium Manager handles it
    wait   = WebDriverWait(driver, 20)

    try:
        # ── 1. Load login page ─────────────────────────────────────────────
        driver.get(ATM_LOGIN_URL)
        wait.until(EC.presence_of_element_located((By.ID, "ctl00_BodyContent_UserName")))

        # ── 2. Enter credentials and submit ───────────────────────────────
        driver.find_element(By.ID, "ctl00_BodyContent_UserName").send_keys(ATM_USERNAME)
        driver.find_element(By.ID, "ctl00_BodyContent_Password").send_keys(ATM_PASSWORD)
        driver.find_element(By.ID, "ctl00_BodyContent_LoginButton").click()
        time.sleep(3)

        # ── 3. Detect password expiration popup ───────────────────────────
        # TMS shows a modal with id="ctl00_BodyContent_ExpiredPasswordPanel"
        # when the password is about to expire. It has two buttons:
        #   "Reset Password"  → id="ctl00_BodyContent_BtnReset"
        #   "Remind me later" → id="ctl00_BodyContent_BtnDontReset"
        # We detect it here and alert you to act manually.
        try:
            expire_panel = driver.find_element(By.ID, "ctl00_BodyContent_ExpiredPasswordPanel")
            if expire_panel.is_displayed():
                try:
                    expire_msg = driver.find_element(
                        By.ID, "ctl00_BodyContent_PasswordExpireMessage"
                    ).text.strip()
                except Exception:
                    expire_msg = "(message not readable)"

                raise LoginIssue(
                    "PASSWORD_EXPIRING",
                    f"⚠️  Your TMS password is expiring soon.\n"
                    f"  TMS message: \"{expire_msg}\"\n\n"
                    f"  Log in manually and click 'Reset Password' to update it\n"
                    f"  before it fully expires and locks you out."
                )
        except LoginIssue:
            raise
        except Exception:
            pass  # Panel not visible — normal, continue

        # ── 4. Detect login failure ────────────────────────────────────────
        # TMS shows errors in #ctl00_BodyContent_InvalidLogin (bold red)
        # and #ctl00_BodyContent_ErrorLabel for other errors
        for error_id in ["ctl00_BodyContent_InvalidLogin", "ctl00_BodyContent_ErrorLabel"]:
            try:
                err_text = driver.find_element(By.ID, error_id).text.strip()
                if err_text:
                    raise LoginIssue(
                        "LOGIN_FAILED",
                        f"🚫 TMS login failed.\n"
                        f"  Error shown: \"{err_text}\"\n"
                        f"  Check your ATM_USERNAME / ATM_PASSWORD in the script.\n"
                        f"  Also check if your account is locked."
                    )
            except LoginIssue:
                raise
            except Exception:
                pass

        # ── 5. Confirm we left the login page ─────────────────────────────
        if "Login.aspx" in driver.current_url:
            driver.save_screenshot("atm_login_debug.png")
            raise LoginIssue(
                "LOGIN_STUCK",
                f"🚫 Still on the login page after submitting.\n"
                f"  This usually means wrong credentials or an unexpected prompt.\n"
                f"  Screenshot saved: atm_login_debug.png"
            )

        # ── 6. Go directly to this terminal's detail page ─────────────────
        driver.get(ATM_TERMINAL_URL)

        # ── 7. Read the cash balance from the cassettes grid ──────────────
        # Row 2, column 5 of the CassettesGridView table
        try:
            balance_el = wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@id="ctl00_BodyContent_CassettesGridView"]/tbody/tr[2]/td[5]')
                )
            )
            balance_text = balance_el.text.strip()
        except Exception:
            driver.save_screenshot("atm_balance_debug.png")
            raise LoginIssue(
                "BALANCE_NOT_FOUND",
                f"⚠️  Logged in but couldn't find the balance field on the terminal page.\n"
                f"  URL used: {ATM_TERMINAL_URL}\n"
                f"  Screenshot saved: atm_balance_debug.png\n"
                f"  Make sure ATM_TERMINAL_URL points to your specific terminal's detail page."
            )

        if not balance_text:
            raise ValueError("Balance element was empty — terminal page may still be loading.")

        balance = parse_balance(balance_text)
        if balance is None:
            raise ValueError(f"Could not parse a number from: '{balance_text}'")

        return balance

    finally:
        driver.quit()


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