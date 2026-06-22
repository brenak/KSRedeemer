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
                    "• Updates player nicknames from the game\n"
                    "• Shows success/failure for each player\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="➕ /add <player_id>",
                value=(
                    "Add a new player to the redemption list.\n"
                    "**Example:** `/add 48666532`\n"
                    "• Checks if player already exists\n"
                    "• Creates placeholder nickname\n"
                    "• Nickname auto-updates on first redemption"
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
                    "• Displays player nicknames and IDs"
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
                text="💡 Tip: Player data persists across bot restarts • Nicknames auto-sync from the game"
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            error_message = f"❌ Error displaying help: {str(e)}"
            await interaction.followup.send(error_message)
            print(f"Error in help command: {e}")
