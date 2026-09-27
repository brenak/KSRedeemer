"""Follow-up sending that survives Discord's 15-minute interaction window.

A slash command's follow-up webhook token expires 15 minutes after the
command was used. Queued work (/add, /catchup, /syncalliance, /refreshname
all) can easily start or finish later than that when the queue is busy, and
followup.send then fails with 401 "Invalid Webhook Token" (50027). Fall back
to posting in the command's channel, mentioning the user, and never raise --
a lost status message must not abort the work that's still in progress.
"""

import discord

# Interaction token errors: 50027 invalid webhook token, 10015 unknown webhook
_EXPIRED_TOKEN_CODES = {50027, 10015}


async def send_followup(interaction: discord.Interaction, content: str = None, **kwargs) -> None:
    try:
        await interaction.followup.send(content, **kwargs)
        return
    except discord.HTTPException as e:
        if e.code not in _EXPIRED_TOKEN_CODES:
            print(f"⚠️ Follow-up send failed ({e}); falling back to channel message")
    except Exception as e:
        print(f"⚠️ Follow-up send failed ({e}); falling back to channel message")

    try:
        channel = interaction.client.get_channel(interaction.channel_id) or interaction.channel
        if channel is None:
            print("⚠️ Could not find the command's channel to post the result")
            return
        mention = interaction.user.mention if interaction.user else ""
        if content is not None:
            content = f"{mention} {content}".strip()
            if len(content) > 2000:
                content = content[:1985] + "\n…(truncated)"
        else:
            content = mention or None
        kwargs.pop("ephemeral", None)
        await channel.send(content, **kwargs)
    except Exception as e:
        print(f"❌ Could not deliver command result to channel: {e}")
