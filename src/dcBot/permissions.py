import discord
from typing import Dict, Any


SETUP_INCOMPLETE_MESSAGE = "Bot setup is not complete. Please use `/setup` to configure the bot."


def ensure_bot_setup(bot_data: Dict[str, Any]) -> str:
    bot_config = bot_data.get("botConfig", {})
    allowed_channel_id = bot_config.get("allowed_channel")
    admin_role_id = bot_config.get("admin_role")

    if not allowed_channel_id or not admin_role_id:
        return SETUP_INCOMPLETE_MESSAGE

    return ""


def check_channel_only(interaction: discord.Interaction, bot_data: Dict[str, Any]) -> str:
    setup_error = ensure_bot_setup(bot_data)
    if setup_error:
        return setup_error

    bot_config = bot_data.get("botConfig", {})
    allowed_channel_id = bot_config.get("allowed_channel")

    if interaction.channel.id != allowed_channel_id:
        allowed_channel = interaction.guild.get_channel(allowed_channel_id)
        return (
            f"This command can only be used in {allowed_channel.mention if allowed_channel else 'the configured channel'}."
        )

    return ""


def check_permissions(interaction: discord.Interaction, bot_data: Dict[str, Any]) -> str:
    channel_error = check_channel_only(interaction, bot_data)
    if channel_error:
        return channel_error

    bot_config = bot_data.get("botConfig", {})
    admin_role_id = bot_config.get("admin_role")

    # Use the raw role IDs from the interaction payload (_roles is a SnowflakeList
    # populated directly from Discord's data, not from the guild cache).  This
    # avoids the failure mode where discord.utils.get(guild.roles, ...) returns
    # None because the guild cache is stale or not yet fully populated, which
    # caused every non-@everyone role check to silently fail for everyone.
    member = interaction.user
    user_role_ids = set(getattr(member, "_roles", []))
    # @everyone has id == guild.id and is always implicit; add it so that
    # configuring @everyone as the admin role continues to work.
    if interaction.guild:
        user_role_ids.add(interaction.guild.id)

    if admin_role_id not in user_role_ids:
        return "You do not have the required role to use this command."

    return ""
