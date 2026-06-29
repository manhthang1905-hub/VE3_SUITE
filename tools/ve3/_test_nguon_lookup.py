"""Lookup TH1-0021 in NGUON sheet to find its channel."""
import sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path

config_dir = Path(r'D:\VE3_SUITE\config')
cfg = json.loads((config_dir / 'config.json').read_text(encoding='utf-8'))
sa_path = config_dir / cfg.get('SERVICE_ACCOUNT_JSON', 'creds.json')
spreadsheet_name = cfg.get('SPREADSHEET_NAME', '')
print(f"Sheet: {spreadsheet_name}, SA exists: {sa_path.exists()}")

import gspread
from google.oauth2.service_account import Credentials
scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly', 'https://www.googleapis.com/auth/drive.readonly']
creds = Credentials.from_service_account_file(str(sa_path), scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open(spreadsheet_name)
ws = sh.worksheet('NGUON')
rows = ws.get_all_values()
print(f"NGUON sheet: {len(rows)} rows")

# Find TH1-0021
code = 'TH1-0021'
found = False
for row in rows[1:]:
    if len(row) > 6 and row[6].strip().upper() == code.upper():
        channel = row[11] if len(row) > 11 else "?"
        print(f"FOUND: {code} -> channel = '{channel}'")
        print(f"  Row cols 6-12: {row[6:13]}")
        found = True
        break

if not found:
    print(f"{code} NOT FOUND in sheet!")
    th1_rows = [r for r in rows[1:] if len(r) > 6 and r[6].strip().upper().startswith('TH1')]
    print(f"All TH1 entries ({len(th1_rows)}):")
    for r in th1_rows[:10]:
        ch = r[11] if len(r) > 11 else "?"
        print(f"  {r[6]} -> channel '{ch}'")
