import json
import os
import discord
from discord import app_commands

from dcBot.commands.redeemCmd import register_redeem_command  # noqa: E402
from dcBot.commands.listCmd import register_list_command  # noqa: E402
from dcBot.commands.addCmd import register_add_command  # noqa: E402
from dcBot.commands.removeCmd import register_remove_command  # noqa: E402
from dcBot.commands.findCmd import register_find_command  # noqa: E402
from dcBot.commands.helpCmd import register_help_command  # noqa: E402
from dcBot.commands.codesCmd import register_codes_command  # noqa: E402
from dcBot.commands.setupCmd import register_setup_command
from dcBot.commands.setCheckIntervalCmd import register_set_check_interval_command
from dcBot.commands.catchupCmd import register_catchup_command
from dcBot.commands.setKingdomCmd import register_set_kingdom_command
from dcBot.commands.setNameCmd import register_set_name_command
from dcBot.commands.refreshNameCmd import register_refresh_name_command
from dcBot.data_handler import load_bot_data, save_bot_data
from dcBot.update_checker import UpdateChecker
from dcBot.gift_code_cache import GiftCodeCacheManager
from dcBot.add_queue import AddQueue
from services.kingshot_client import KingshotClient


def load_bot_data_with_players():
    bot_data = load_bot_data()
    if "players" not in bot_data:
        bot_data["players"] = []
    return bot_data


def save_bot_data_with_players(data):
    save_bot_data(data)


def init_bot(token: str) -> tuple[discord.Client, KingshotClient]:
    if not token:
        raise ValueError("Discord token cannot be empty")

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    bot_data = load_bot_data_with_players()
    add_queue = AddQueue()
    kingshot_client = KingshotClient()

    # Register commands
    register_redeem_command(tree, bot_data, save_bot_data_with_players, kingshot_client)
    register_list_command(tree, bot_data)
    register_add_command(tree, bot_data, save_bot_data_with_players, add_queue, kingshot_client)
    register_remove_command(tree, bot_data, save_bot_data_with_players)
    register_set_kingdom_command(tree, bot_data, save_bot_data_with_players)
    register_set_name_command(tree, bot_data, save_bot_data_with_players)
    register_refresh_name_command(tree, bot_data, save_bot_data_with_players, kingshot_client)
    register_find_command(tree, bot_data)
    register_help_command(tree, bot_data)
    register_codes_command(tree, bot_data)
    register_setup_command(tree, save_bot_data_with_players, bot_data)

    # Initialize UpdateChecker
    client.update_checker = UpdateChecker(client, bot_data, save_bot_data_with_players)

    # Initialize GiftCodeCacheManager
    cache_manager = GiftCodeCacheManager(client, bot_data, save_bot_data_with_players, kingshot_client)
    client.gift_code_cache = cache_manager
    register_set_check_interval_command(tree, bot_data, cache_manager)
    register_catchup_command(tree, bot_data, save_bot_data_with_players, add_queue, kingshot_client)

    @client.event
    async def on_ready():
        add_queue.start()
        guild = discord.Object(id=1482088126640947483)
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        print(f"✅ Logged in as {client.user} · commands synced")

    @client.event
    async def on_disconnect():
        await kingshot_client.close()

    return client, kingshot_client


async def start_bot(token: str):
    client, kingshot_client = init_bot(token)
    try:
        await client.start(token)
    finally:
        await kingshot_client.close()
