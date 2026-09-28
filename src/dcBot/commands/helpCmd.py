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
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="➕ /add <player_id> [kingdom] [name]",
                value=(
                    "Add a new player to the redemption list.\n"
                    "**Examples:** `/add 48666532` or `/add 48666532 1300 Syde`\n"
                    "• Looks up the in-game name, kingdom and alliance from MightPulse\n"
                    "• `kingdom` is only used if MightPulse can't find the player (defaults to `1259`)\n"
                    "• `name` sets a custom name that automatic refreshes keep\n"
                    "• Auto-redeems all active codes for the new player\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="🏰 /setkingdom <player_id> <kingdom>",
                value=(
                    "Manually update a player's kingdom number.\n"
                    "**Example:** `/setkingdom 48666532 1300`\n"
                    "• Usually not needed — kingdoms update from MightPulse automatically\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="🏷️ /setname <player_id> <name>",
                value=(
                    "Manually set/tag a player's name.\n"
                    "**Example:** `/setname 48666532 Syde`\n"
                    "• Automatic refreshes keep this name (kingdom/alliance still update)\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="🔃 /refreshname <player_id | all>",
                value=(
                    "Re-look up name, kingdom and alliance from MightPulse.\n"
                    "**Examples:** `/refreshname 48666532` or `/refreshname all`\n"
                    "• A single-player refresh replaces a custom `/setname` name\n"
                    "• `all` uses alliance rosters first to save API quota\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="🏰 /syncalliance [kingdom] [tag]",
                value=(
                    "Add every member of an alliance and keep it tracked.\n"
                    "**Examples:** `/syncalliance 1343 2mk` or `/syncalliance` (all tracked)\n"
                    "• Tag is case-sensitive\n"
                    "• New members get all active codes\n"
                    "• Tracked alliances re-sync daily and whenever a new code appears\n"
                    "• `/alliances` lists tracked alliances; `/untrackalliance <kingdom> <tag>` stops tracking "
                    "(members stay registered)\n"
                    "• Requires bot admin permissions"
                ),
                inline=False,
            )

            embed.add_field(
                name="📋 /sheet sync [tab] [dry_run]",
                value=(
                    "Sync alliance roster sheet tabs against live MightPulse data (all tabs if `tab` omitted).\n"
                    "• `/sheet add <kingdom> <tag> [tab]`, `/sheet remove <tab>`, `/sheet list` "
                    "manage which alliance goes in which tab\n"
                    "• Also refreshes the `Analytics` tab (TG5 pie, power & TC charts); "
                    "`/sheet analytics` refreshes just that\n"
                    "• 🟩 position matches · 🟨 position changed (see `observed_x/y`) · "
                    "🟪 left the alliance (see `observed_tag`)\n"
                    "• Updates names and `Kingdom`, adds missing members; never changes `x`/`y`/`Current_Tag` "
                    "— differences go in `observed_*`\n"
                    "• One lookup per member — takes a couple of minutes\n"
                    "• `dry_run: True` previews counts without writing\n"
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
                    "• Displays player nicknames, IDs, kingdoms and current alliances"
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
                text="💡 Tip: Player data persists across bot restarts"
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            error_message = f"❌ Error displaying help: {str(e)}"
            await interaction.followup.send(error_message)
            print(f"Error in help command: {e}")
