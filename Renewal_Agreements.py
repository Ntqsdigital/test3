import os
import gdown
import pandas as pd
from datetime import datetime, date
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
import logging

# -------------------- Configuration --------------------
SENDER_EMAIL = os.getenv("APP_EMAIL", "ganeshsai@nuevostech.com")
APP_PASSWORD = os.getenv("APP_PASSWORD", "myrvnqpycpouccwb")   # use env var in production
RECIPIENTS = [os.getenv("RECIPIENT_DEFAULT", "")]
FILE_ID = os.getenv("FILE_ID", "1aEyOe-C98I_sV0AItEewMFBl1l5R85R2")
DOWNLOAD_PATH = Path(os.getenv("DOWNLOAD_PATH", "Renewal.xlsx"))
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SEND_CONFIRMATION = False   # set True if you want the one-time confirmation email each run
LOG_FILE = Path(os.getenv("LOG_FILE", "renewal.log"))
# -------------------------------------------------------

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

TEMPLATE_SUBJECT = 'Renewed Expiry Date Notification'
TEMPLATE_BODY = """Hi {Client},

Your product's expiry date has been renewed.
New Expiry Date: {new_expiry}

• Attached File Path: {file_path}

Please take note and let us know if you have any questions.

Regards,
NTQS Digital

Note: Your agreement expires in {days_left} day(s)
"""

def download_from_drive(file_id: str, dest: Path):
    url = f"https://drive.google.com/uc?id={file_id}&export=download"
    logging.info(f"Downloading {url} -> {dest} ...")
    try:
        gdown.download(url, str(dest), quiet=False)
        logging.info("Download complete.")
    except Exception as e:
        logging.exception("Failed to download file from Google Drive.")
        raise

def detect_header_and_load(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path, header=None, engine="openpyxl")
    header_idx = None
    search_tokens = {"expiry", "expiry date", "email", "name", "file", "due", "end", "expires", "expires on", "due date", "client", "contact"}
    max_scan = min(50, len(raw))
    for i in range(max_scan):
        row_vals = [str(x).strip().lower() for x in raw.iloc[i].fillna("")]
        combined = " ".join(row_vals)
        if any(tok in combined for tok in search_tokens):
            header_idx = i
            logging.info(f"Detected header at row {header_idx} (0-based). Row values: {row_vals}")
            break
    if header_idx is None:
        header_idx = 0
        logging.warning("Could not confidently detect header row; using row 0 as header.")

    df = pd.read_excel(path, header=header_idx, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all").reset_index(drop=True)
    return df

def build_message(sender: str, to_list: list, subject: str, body: str, attachment_path: str = None) -> EmailMessage:
    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = ", ".join(to_list)
    msg['Subject'] = subject
    msg.set_content(body, subtype='plain', charset='utf-8')

    # Attach file if provided and exists
    if attachment_path:
        path = Path(attachment_path)
        if path.exists() and path.is_file():
            with open(path, 'rb') as f:
                file_data = f.read()
                maintype, subtype = 'application', 'octet-stream'
                msg.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=path.name)
                logging.info(f"Attached file '{path.name}' to email.")
        else:
            logging.warning(f"Attachment file not found or invalid: {attachment_path}")
    return msg

def send_email(msg: EmailMessage):
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(SENDER_EMAIL, APP_PASSWORD)
            smtp.send_message(msg)
        logging.info(f"Email sent to: {msg['To']} | Subject: {msg['Subject']}")
    except smtplib.SMTPAuthenticationError:
        logging.exception("SMTP authentication error. Check app password or account security settings.")
        raise
    except Exception:
        logging.exception("Failed to send email.")
        raise

def make_agreements_list(df: pd.DataFrame) -> list:
    agreements = []
    expiry_cols = [c for c in df.columns if any(tok in c.lower() for tok in ('expiry','due','end','expires'))]
    email_cols = [c for c in df.columns if 'email' in c.lower()]
    file_cols = [c for c in df.columns if 'file' in c.lower() or c.lower() == 'name' or 'file name' in c.lower()]
    path_cols = [c for c in df.columns if 'path' in c.lower()]
    name_cols = [c for c in df.columns if any(tok in c.lower() for tok in ('name','client','contact','customer','person'))]

    if not expiry_cols:
        msg = f"No expiry-like column found. Columns discovered: {list(df.columns)}"
        logging.error(msg)
        raise KeyError(msg)

    expiry_col = expiry_cols[0]
    email_col = email_cols[0] if email_cols else None
    file_col = file_cols[0] if file_cols else None
    path_col = path_cols[0] if path_cols else None
    name_col = name_cols[0] if name_cols else None

    logging.info(f"Using expiry column: '{expiry_col}' | email column: '{email_col}' | file column: '{file_col}' | path column: '{path_col}' | name column: '{name_col}'")

    for idx, row in df.iterrows():
        try:
            expiry_raw = row.get(expiry_col)
            expiry_dt = pd.to_datetime(expiry_raw, errors='coerce', dayfirst=True)
            if pd.isna(expiry_dt):
                logging.debug(f"Skipping row {idx} due to unparseable expiry: {expiry_raw!r}")
                continue

            display_name = ''
            if file_col:
                display_name = str(row.get(file_col, '')).strip()
            if not display_name and name_col:
                display_name = str(row.get(name_col, '')).strip()
            if not display_name and email_col:
                display_name = str(row.get(email_col, '')).strip()
            if not display_name:
                display_name = 'Unnamed Agreement'

            email_val = str(row.get(email_col, '')).strip() if email_col else ''
            name_val = str(row.get(name_col, '')).strip() if name_col else ''

            item = {
                'file': display_name,
                'expiry_date': expiry_dt,
                'path': str(row.get(path_col, '')).strip() if path_col else '',
                'status': 'EXPIRES TODAY' if expiry_dt.date() == datetime.now().date() else 'Upcoming',
                'email': email_val if email_val else RECIPIENTS[0],
                'name': name_val
            }
            agreements.append(item)
        except Exception:
            logging.exception(f"Error parsing row {idx}; skipping.")
            continue
    return agreements

def send_confirmation_email(client_name: str = "Client", to_emails: list = None):
    to_addresses = to_emails if to_emails else RECIPIENTS
    subject = TEMPLATE_SUBJECT
    body = TEMPLATE_BODY.format(Client=client_name, new_expiry="15-09-2025", days_left=0, file_path="N/A")
    msg = build_message(SENDER_EMAIL, to_addresses, subject, body)
    try:
        send_email(msg)
        logging.info("Renewal confirmation email sent.")
    except Exception:
        logging.exception("Error sending renewal confirmation email.")

def send_renewal_reminder(agreement: dict, days_left: int):
    client_display = agreement.get('name') or agreement.get('email') or "Client"
    subject = f"Renewal Reminder: '{agreement['file']}' Expires Soon"

    body = TEMPLATE_BODY.format(
        Client=client_display,
        new_expiry=agreement['expiry_date'].strftime('%Y-%m-%d'),
        days_left=days_left,
        file_path=agreement['path'] if agreement['path'] else "N/A"
    )
    to_addr = [agreement.get('email')] if agreement.get('email') else RECIPIENTS
    msg = build_message(SENDER_EMAIL, to_addr, subject, body, attachment_path=agreement['path'])
    try:
        send_email(msg)
        logging.info(f"Reminder sent for '{agreement['file']}' ({days_left} days left) to {to_addr}")
    except Exception:
        logging.exception(f"Failed to send reminder for '{agreement['file']}'.")

def send_hourly_alert(agreement: dict):
    client_display = agreement.get('name') or agreement.get('email') or "Client"
    subject = f"Renewal Reminder: '{agreement['file']}' Expires Today"

    body = TEMPLATE_BODY.format(
        Client=client_display,
        new_expiry=agreement['expiry_date'].strftime('%Y-%m-%d'),
        days_left=0,
        file_path=agreement['path'] if agreement['path'] else "N/A"
    )
    to_addr = [agreement.get('email')] if agreement.get('email') else RECIPIENTS
    msg = build_message(SENDER_EMAIL, to_addr, subject, body, attachment_path=agreement['path'])
    try:
        send_email(msg)
        logging.info(f"Hourly alert sent for '{agreement['file']}' to {to_addr}")
    except Exception:
        logging.exception(f"Failed to send hourly alert for '{agreement['file']}'.")

def run_reminders_and_alerts(agreements: list):
    today = date.today()
    for agreement in agreements:
        days_left = (agreement['expiry_date'].date() - today).days
        if 1 <= days_left <= 5:
            send_renewal_reminder(agreement, days_left)

    todays = [a for a in agreements if a['expiry_date'].date() == today]
    if not todays:
        logging.info("No agreements expiring today; skipping hourly alerts.")
        return

    logging.info(f"{len(todays)} agreement(s) expiring today. Sending one-shot alerts.")
    for agreement in todays:
        send_hourly_alert(agreement)

def main_run_once():
    try:
        download_from_drive(FILE_ID, DOWNLOAD_PATH)
    except Exception:
        logging.exception("Download failed. Exiting.")
        return

    try:
        df = detect_header_and_load(DOWNLOAD_PATH)
    except Exception:
        logging.exception("Reading Excel failed. Exiting.")
        return

    logging.info("Excel Preview (first 8 rows):\n" + df.head(8).to_string(index=False))
    logging.info("Detected columns: " + ", ".join(list(df.columns)))

    try:
        agreements = make_agreements_list(df)
        logging.info(f"Parsed {len(agreements)} agreement(s).")
        if agreements:
            logging.info("Sample parsed agreements:\n" + "\n".join(
                [f"  - {a['file']} | {a['expiry_date'].strftime('%Y-%m-%d')} | {a['email']} | name={a.get('name','')}" for a in agreements[:10]]
            ))
    except KeyError as e:
        logging.error("Could not find expiry column; exiting.")
        return
    except Exception:
        logging.exception("Failed to build agreements list; exiting.")
        return

    if SEND_CONFIRMATION:
        if agreements:
            first = agreements[0]
            send_confirmation_email(client_name=first.get('name') or first.get('email'), to_emails=[first.get('email')])
        else:
            send_confirmation_email()

    run_reminders_and_alerts(agreements)

if __name__ == "__main__":
    main_run_once()

