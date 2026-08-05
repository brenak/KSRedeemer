import discord
from discord import app_commands

from dcBot.permissions import check_channel_only


def register_help_command(tree: app_commands.CommandTree, bot_data):
    @tree.command(name="help", description="Display all available commands and usage")
    async def help_command(interaction: discord.Interaction):
        permission_error = check_channel_only(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            embed = discord.Embed(
                title="📚 Kingshot Redeemer Bot - Help",
                description="Here are all available commands:",
                color=discord.Color.blue(),
            )

            embed.add_field(
                name="⚙️ /setup <channel> <admin_role>",
                value=(
                    "Configure the allowed channel and admin role. Must be run once before other commands.\n"
                    "• Requires bot admin permissions\n"
                    "• Example: `/setup #redeem @Kingshot Admins`"
                ),
                inline=False,
            )

            embed.add_field(
                name="🎁 /redeem <gift_code> [player_id]",
                value=(
                    "Redeem a Kingshot gift code for all players or a single player ID.\n"
                    "**Examples:** `/redeem KSFB15K` or `/redeem KSFB15K 48666532`\n"
                    "• Shows success/failure for each player\n"
                    "• Refreshes kingdom `1259` player names from kingshot_web along the way\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="➕ /add <player_id> [kingdom] [name]",
                value=(
                    "Add a new player to the redemption list.\n"
                    "**Examples:** `/add 48666532` or `/add 48666532 1300 Syde`\n"
                    "• Checks if player already exists\n"
                    "• Kingdom defaults to `1259` if omitted\n"
                    "• Kingdom `1259` players auto-look up their real name from kingshot_web "
                    "unless `name` is given\n"
                    "• Non-1259 players fall back to a placeholder nickname unless `name` is given "
                    "— use `name` to tag/label them\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="🏰 /setkingdom <player_id> <kingdom>",
                value=(
                    "Update a player's kingdom number (e.g. after they migrate).\n"
                    "**Example:** `/setkingdom 48666532 1300`\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="🏷️ /setname <player_id> <name>",
                value=(
                    "Manually set/tag a player's name (any kingdom).\n"
                    "**Example:** `/setname 48666532 Syde`\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="🔃 /refreshname <player_id>",
                value=(
                    "Re-look up a kingdom `1259` player's real name from kingshot_web.\n"
                    "**Example:** `/refreshname 48666532`\n"
                    "• Only works for kingdom `1259` players — others should use `/setname`\n"
                    "• No-op if the player isn't Intel-confirmed in kingshot_web yet\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="➖ /remove <query>",
                value=(
                    "Remove a player by ID or nickname.\n"
                    "**Examples:**\n"
                    "• `/remove 123456789` (exact ID)\n"
                    "• `/remove Jareggie` (partial nickname match)\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="📋 /list",
                value=(
                    "View all registered players with pagination.\n"
                    "• Shows 10 players per page\n"
                    "• Navigate with ◀️ Previous / Next ▶️ buttons\n"
                    "• Displays player nicknames, IDs, and kingdoms"
                ),
                inline=False,
            )

            embed.add_field(
                name="🔎 /find <query>",
                value=(
                    "Search for a specific player by ID or nickname.\n"
                    "**Examples:**\n"
                    "• `/find 48666532` (exact ID)\n"
                    "• `/find Syde` (partial nickname match)\n"
                    "• Shows up to 10 matching results"
                ),
                inline=False,
            )

            embed.add_field(
                name="🎁 /codes",
                value=(
                    "View all currently active gift codes.\n"
                    "• Shows codes that are valid and not expired\n"
                    "• Shows source (API or Wiki) for each code\n"
                    "• Displays expiration dates"
                ),
                inline=False,
            )

            embed.add_field(
                name="🔁 /catchup [player_id]",
                value=(
                    "Redeem any active codes a player hasn't received yet.\n"
                    "• Omit `player_id` to catch up all players\n"
                    "• Queued behind any in-progress `/add` requests\n"
                    "• Reports per-code results and marks expired codes\n"
                    "• Refreshes kingdom `1259` player names from kingshot_web along the way\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="⏱️ /set-check-interval <hours>",
                value=(
                    "Set how often the bot checks for new gift codes (minimum 1 hour).\n"
                    "• Takes effect immediately without a redeploy\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="🔄 Auto-Update Check",
                value=(
                    "The bot automatically checks for updates on Docker Hub every 24 hours.\n"
                    "• Notifications are sent to the configured channel\n"
                    "• Checks against `brenak/kingshot-redeemer:latest`"
                ),
                inline=False,
            )

            embed.add_field(
                name="❓ /help",
                value="Display this help message.",
                inline=False,
            )

            embed.set_footer(
                text="💡 Tip: Player data persists across bot restarts"
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            error_message = f"❌ Error displaying help: {str(e)}"
            await interaction.followup.send(error_message)
            print(f"Error in help command: {e}")
