import discord
from discord import app_commands
from typing import Dict, Any

from dcBot.permissions import check_channel_only


def register_codes_command(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
):

    @tree.command(name="codes", description="List all cached gift codes and their status")
    async def list_codes(interaction: discord.Interaction):
        permission_error = check_channel_only(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            gift_code_cache = bot_data.get("gift_code_cache", {})

            if not gift_code_cache:
                await interaction.followup.send("❌ No codes in cache yet.")
                return

            valid_codes = [
                (code, data) for code, data in gift_code_cache.items()
                if data.get("status") == "valid"
            ]

            if not valid_codes:
                await interaction.followup.send("❌ No active codes available.")
                return

            embed = discord.Embed(
                title="🎁 Active Gift Codes",
                color=discord.Color.green(),
            )

            codes_text = ""
            for code, data in valid_codes:
                expires = data.get("expires") or "Never"
                source = data.get("source", "api")
                source_label = "🌐 API" if source == "api" else "📖 Wiki"
                codes_text += f"✅ `{code}` — {source_label}\n   Expires: {expires}\n"

            embed.add_field(
                name=f"Active Codes ({len(valid_codes)})",
                value=codes_text,
                inline=False,
            )

            interval = bot_data.get("botConfig", {}).get("gift_code_check_interval_hours") or 1
            embed.set_footer(text=f"Cache updated every {interval} hour(s)")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            error_message = f"❌ Error loading codes: {str(e)}"
            await interaction.followup.send(error_message)
            print(f"Error in codes command: {e}")
