#!/usr/bin/env python3
import os
import json
import sys
from datetime import datetime

# Google API Imports — assumes venv site-packages are in sys.path
PARENT_DIR = os.path.join(os.path.dirname(__file__), '..')
venv_path  = os.path.join(PARENT_DIR, 'venv', 'lib', 'python3.13', 'site-packages')
sys.path.insert(0, venv_path)

import gspread
from google.oauth2.service_account import Credentials

# === CONFIGURATION ===
BASE_DIR         = os.path.dirname(__file__)

NUEVOS_JSON      = os.path.join(BASE_DIR, 'nuevos_rumbo_areas.json')
CREDENTIALS_PATH = os.path.join(PARENT_DIR, 'credentials_autoconocimiento.json')
LOG_FILE         = os.path.join(BASE_DIR, 'logs_rumbo_areas.txt')

SHEET_ID = '1-pHNm73qCxtGdliv3-_gxdGg1F2uVyWgn6PGNveE8ig'
SHEET_NAME = 'Hoja 2'

def log(message: str):
    """Append a message with timestamp to logs.txt"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, 'a', encoding='utf-8') as logf:
        logf.write(f"[{timestamp}] {message}\n")

# === AUTH ===
scopes = ['https://www.googleapis.com/auth/spreadsheets']
creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
gc = gspread.authorize(creds)
ws = gc.open_by_key(SHEET_ID).worksheet(SHEET_NAME)

# === READ EMAILS ===
if not os.path.exists(NUEVOS_JSON):
    msg = "❌ No se encontró el archivo nuevos_usuarios.json."
    print(msg)
    log(msg)
    sys.exit(1)

with open(NUEVOS_JSON, 'r', encoding='utf-8') as f:
    new_emails = json.load(f)

if not new_emails:
    msg = "✅ sender.py ran via importar_autoconocimiento.php — no new users to append, all good."
    os.remove(NUEVOS_JSON)
    msg2 = "🧹 nuevos_usuarios.json eliminado."
    print(msg)
    print(msg2)
    log(msg)
    sys.exit(0)


# === WRITE TO SHEET ===
rows = [[email] for email in new_emails]
ws.append_rows(rows, value_input_option='RAW')

# === Confirm & Cleanup ===
msg = f"📤 {len(new_emails)} emails enviados a Google Sheets."
print(msg)
log(msg)

msg = f"✅ sender.py ran via importar_autoconocimiento.php — {len(new_emails)} users appended."
print(msg)
log(msg)


os.remove(NUEVOS_JSON)
msg = "🧹 nuevos_usuarios.json eliminado."
print(msg)
log(msg)
