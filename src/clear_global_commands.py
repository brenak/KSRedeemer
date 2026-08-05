"""One-off maintenance script: wipes GLOBAL slash commands.

ksRedeemBot.py switched from global command sync (`await tree.sync()`) to
guild-scoped sync (commit d725d72), but Discord doesn't retire old global
command registrations on its own -- they stay live indefinitely until
explicitly cleared, showing up in the guild's command picker alongside the
current guild-scoped versions (duplicate /add entries, etc).

Run this once whenever that happens. Global command removal can take up to
an hour to fully propagate on Discord's side (same caching the README's
"Commands not appearing" troubleshooting section already covers) -- the
duplicate may not disappear from your client immediately.

Usage: python clear_global_commands.py
"""
import asyncio
import discord
from discord import app_commands

from config.config import DISCORD_TOKEN


async def main():
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    @client.event
    async def on_ready():
        tree.clear_commands(guild=None)
        await tree.sync()
        print(f"✅ Cleared global commands (logged in as {client.user}). "
              f"May take up to an hour to disappear from Discord's client cache.")
        await client.close()

    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
