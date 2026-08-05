import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "500"))
GIFT_CODE_CHECK_INTERVAL_HOURS = max(1, int(os.getenv("GIFT_CODE_CHECK_INTERVAL_HOURS", "1")))

# kingshot_web deployment + login, used to look up a player's current
# nickname by FID for kingdom 1259 (see services/kingshot_client.py). Same
# env var names and same account as KSCompanion, which already logs into
# kingshot_web this way. Feature is silently disabled (lookups always
# return None) if any of these are left unset.
KINGSHOT_URL = os.getenv("KINGSHOT_URL", "").rstrip("/")
KINGSHOT_USERNAME = os.getenv("KINGSHOT_USERNAME", "")
KINGSHOT_PASSWORD = os.getenv("KINGSHOT_PASSWORD", "")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is not set.")