import asyncio
import sys

from config.config import DISCORD_TOKEN  # noqa: E402
from dcBot.init_check import ensure_bot_data_json_exists  # noqa: E402
from dcBot.ksRedeemBot import start_bot  # noqa: E402
from services.kshive_links import status_line as kshive_status_line  # noqa: E402


async def main():
    ensure_bot_data_json_exists()

    print("🚀 Starting Kingshot Redeemer Bot...")
    print(kshive_status_line())

    if DISCORD_TOKEN is None:
        print("❌ DISCORD_TOKEN is not set")
        sys.exit(1)

    try:
        await start_bot(DISCORD_TOKEN)
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())