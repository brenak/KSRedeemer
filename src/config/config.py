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

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is not set.")