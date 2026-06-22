import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "500"))
GIFT_CODE_CHECK_INTERVAL_HOURS = max(1, int(os.getenv("GIFT_CODE_CHECK_INTERVAL_HOURS", "1")))

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is not set.")