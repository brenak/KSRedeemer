import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
# Server slash commands are synced to on startup. Guild-scoped sync shows up
# almost instantly, unlike global sync (up to an hour). One bot instance
# serves one server: botData.json holds a single /setup + player roster, so
# a second server gets its own deployment with its own DISCORD_GUILD_ID.
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1530565101453840434"))
GIFT_CODE_CHECK_INTERVAL_HOURS = max(1, int(os.getenv("GIFT_CODE_CHECK_INTERVAL_HOURS", "1")))

# MightPulse API key (https://api.mightpulse.com/), used to look up players
# (nickname, kingdom, alliance) by FID and to pull alliance rosters -- see
# services/mightpulse_client.py. Lookups and alliance sync are silently
# disabled if left unset.
MIGHTPULSE_API_KEY = os.getenv("MIGHTPULSE_API_KEY", "").strip()

# Alliance roster Google Sheet sync (/sheet, services/sheet_sync.py).
# Authenticates as a Google service account whose JSON key lives at
# GOOGLE_SHEETS_CREDENTIALS_PATH; the spreadsheet must be shared with that
# account's client_email as Editor. /sheet sync reports "not configured" if
# the key file is missing -- nothing else in the bot depends on it.
GOOGLE_SHEETS_CREDENTIALS_PATH = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH", "").strip()
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1EQaEx2ISCMAQmHsd71S-GcCoLL58ZJxtBykhWFss11A").strip()
# Which alliance -> tab the sheet sync starts with. Only seeds the list the
# first time; after that the list lives in botData.json and is managed with
# /sheet add / remove (one tab per alliance, all in the same spreadsheet).
SHEET_DEFAULT_KINGDOM = os.getenv("SHEET_DEFAULT_KINGDOM", "1343").strip()
SHEET_DEFAULT_TAG = os.getenv("SHEET_DEFAULT_TAG", "1MK").strip()
SHEET_DEFAULT_TAB = os.getenv("SHEET_DEFAULT_TAB", "1MK").strip()
# Automatic full /sheet sync (every tab + Analytics) every N hours; 0 = off.
SHEET_AUTO_SYNC_HOURS = max(0, int(os.getenv("SHEET_AUTO_SYNC_HOURS", "24") or 0))

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is not set.")