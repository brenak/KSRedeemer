# Kingshot Redeemer Bot

A Discord bot that automates gift code redemption for Kingshot players by calling the same signed API the official redemption page uses. Redeem codes for multiple accounts simultaneously with a single command.


## Features

- 🎁 **Bulk Redemption** - Redeem gift codes for all registered players at once
- ⚡ **Direct API** - Calls Kingshot's gift-code API directly (no browser automation)
- 💾 **Auto-Sync Player Info** - Nicknames, kingdoms and alliances come from the [MightPulse API](https://api.mightpulse.com/): looked up on `/add`, and refreshed only when a new gift code appears (or via `/refreshname`) to stay within the API's rate limits. Kingdom transfers are picked up automatically, so redemption keeps working
- 🏰 **Alliance Sync** - `/syncalliance <kingdom> <tag>` registers every alliance member; tracked alliances re-sync daily and whenever a new code appears, and new members get all active codes
- 📋 **Player Management** - Add, remove, search, and list players
- 🔄 **Auto-Update Check** - Automatically checks for new Docker image versions every 24h
- 🐳 **Docker Ready** - Easy deployment with Docker/Docker Compose
- 🔄 **Cross-Platform** - Supports AMD64 and ARM64 architectures

## Quick Start

### Prerequisites

1. A Discord bot token ([Get one here](#discord-bot-setup))
2. Docker installed on your system

### Run with Docker

**Option 1: Docker Run**

```bash
docker run -d \
  --name kingshot-redeemer \
  -e DISCORD_TOKEN=your_discord_token_here \
  -v kingshot-data:/data \
  --restart unless-stopped \
  jarecoder/kingshot-redeemer:latest
```

**Option 2: Docker Compose** (Recommended)

1. Create a `docker-compose.yml` file:

```yaml
version: '3.8'

services:
  kingshot-redeemer:
    image: jarecoder/kingshot-redeemer:latest
    container_name: kingshot-redeemer-bot
    restart: unless-stopped
    environment:
      - DISCORD_TOKEN=${DISCORD_TOKEN}
    volumes:
      - kingshot-data:/data

volumes:
  kingshot-data:
```

2. Create a `.env` file:

```env
DISCORD_TOKEN=your_discord_token_here
```

3. Start the bot:

```bash
docker compose up -d
```

4. View logs:

```bash
docker compose logs -f
```

## Discord Bot Setup

### 1. Create a Discord Application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **"New Application"**
3. Give it a name (e.g., "Kingshot Redeemer")
4. Click **"Create"**

### 2. Set Install Link to None for Private Bot

1. Go to the ⁠Discord Developer Portal and select your application.
2. Navigate to the Installation tab in the left-hand menu.
3. Scroll down to the Install Link section and change the setting to None.
4. Click Save Changes at the bottom of the screen

### 3. Create a Bot User

1. In your application, go to the **"Bot"** tab
2. Click **"Add Bot"** → **"Yes, do it!"**
3. Under the bot's username, click **"Reset Token"** and copy it
   - ⚠️ **Save this token securely** - you'll need it for the `DISCORD_TOKEN` environment variable
4. Bot can be public or private up to your choosing. I will always recommend private bots for security reasons.

### 4. Invite the Bot to Your Server

1. Go to the **"OAuth2"** → **"URL Generator"** tab
2. Select these scopes:
   - ✅ `bot`
   - ✅ `applications.commands`
3. Select these bot permissions:
   - ✅ Send Messages
   - ✅ Use Slash Commands
4. Copy the generated URL and open it in your browser
5. Select your server and authorize

## Discord Commands

| Command | Description | Example |
|---------|-------------|----------|
| `/setup <channel> <role>` | Configure update notifications channel and admin role | `/setup #my-channel @KingshotAdmin` |
| `/redeem <gift_code>` | Redeem a gift code for all registered players | `/redeem KSFB15K` |
| `/add <player_id> [kingdom] [name]` | Add a new player and auto-redeem all active codes for them. Name, kingdom and alliance are looked up from MightPulse; `kingdom` is only a fallback if the player isn't found. `name` sets a custom name that automatic refreshes keep | `/add 123456789 1300 Syde` |
| `/setkingdom <player_id> <kingdom>` | Manually update a player's kingdom (normally updated automatically from MightPulse) | `/setkingdom 123456789 1300` |
| `/setname <player_id> <name>` | Manually set/tag a player's name. Automatic refreshes keep it | `/setname 123456789 Syde` |
| `/refreshname <player_id \| all>` | Re-look up name, kingdom and alliance from MightPulse for one player or everyone. A single-player refresh replaces a custom `/setname` name | `/refreshname all` |
| `/syncalliance [kingdom] [tag]` | Register every member of an alliance (tag is case-sensitive) and track it. New members get all active codes. With no arguments, re-syncs all tracked alliances | `/syncalliance 1343 2mk` |
| `/untrackalliance <kingdom> <tag>` | Stop syncing an alliance; its members stay registered | `/untrackalliance 1343 2mk` |
| `/alliances` | List tracked alliances | `/alliances` |
| `/remove <query>` | Remove a player by ID or nickname | `/remove Jareggie` |
| `/list` | View all registered players (paginated, 10 per page) | `/list` |
| `/find <query>` | Search for a player by ID or nickname | `/find 123456789` |
| `/codes` | List all currently active gift codes and their source (API/Wiki) | `/codes` |
| `/catchup [player_id]` | Redeem any active codes a player (or all players) haven't received yet | `/catchup` |
| `/set-check-interval <hours>` | Set how often the bot checks for new gift codes (min 1 hour) | `/set-check-interval 2` |
| `/help` | Display all available commands and usage | `/help` |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | ✅ Yes | - | Your Discord bot token from the Developer Portal |
| `GIFT_CODE_CHECK_INTERVAL_HOURS` | ❌ No | `1` | How often (in hours) to check for new gift codes. Minimum 1. Can also be changed at runtime with `/set-check-interval` without redeploying. |
| `DISCORD_GUILD_ID` | ❌ No | `1530565101453840434` | Server (guild) ID slash commands are synced to on startup — guild sync makes commands appear almost instantly. One deployment serves one server. |
| `CONTAINER_NAME` / `DATA_VOLUME` | ❌ No | `sdw-redeemer-bot` / `kingshot-data` | docker-compose only. Set both to something unique when running a second copy of the bot on the same host, so it gets its own container and its own player data. |
| `MIGHTPULSE_API_KEY` | ❌ No | - | [MightPulse](https://api.mightpulse.com/) API key (get one via Discord login on their site). Powers player name/kingdom/alliance lookups and alliance sync. Silently disabled if unset. |

### MightPulse rate limits

The API key allows 60 requests/minute and 5,000/day, so the bot is deliberately frugal:

- The routine gift-code check makes **no** MightPulse calls; `/redeem` and `/catchup` don't either.
- When a new code appears, tracked alliance rosters are pulled first (one request per alliance covers every member), then only players outside those rosters are looked up individually. This happens before redeeming, so kingdom transfers are applied first.
- The daily alliance sync costs one request per tracked alliance.
- All requests share a throttle (~1 per 1.1s). A 429 is retried once; if it persists, the batch stops and the rest catch up next time.

## Data Persistence & Backup

Bot data (players, redeemed codes, config) is stored in `/app/data/botData.json` inside the container. The Docker volume `kingshot-data` ensures your data persists across container restarts, image updates, and reboots.

### Automated backups

`backup.sh` copies `botData.json` from the running container to a timestamped file on the host. Set it up once with crontab:

```bash
# Make scripts executable (one-time setup)
chmod +x /home/ubuntu/sdw-redeemer/backup.sh
chmod +x /home/ubuntu/sdw-redeemer/restore.sh

# Open your crontab
crontab -e
```

Add this line to run a backup every day at 2 AM:

```
0 2 * * * /home/ubuntu/sdw-redeemer/backup.sh
```

Backups are saved to `/home/ubuntu/sdw-redeemer/backup/botData_YYYYMMDD_HHMMSS.json`.

### Manual backup

```bash
/home/ubuntu/sdw-redeemer/backup.sh
```

### Restore

```bash
# Restore the most recent backup
/home/ubuntu/sdw-redeemer/restore.sh

# Restore a specific backup by filename
/home/ubuntu/sdw-redeemer/restore.sh botData_20260622_021500.json

# Restore from an absolute path
/home/ubuntu/sdw-redeemer/restore.sh /home/ubuntu/sdw-redeemer/backup/botData_20260622_021500.json
```

`restore.sh` copies the file into the container and restarts it so the bot picks up the changes immediately.

### Manually editing botData.json

If you need to edit the data directly (e.g. to pre-populate `redeemed_codes` for a code):

```bash
# 1. Copy the file out
docker cp sdw-redeemer-bot:/app/data/botData.json ./botData_edit.json

# 2. Edit it (fill in any redeemed_codes arrays, fix player entries, etc.)
nano ./botData_edit.json

# 3. Copy it back and restart
docker cp ./botData_edit.json sdw-redeemer-bot:/app/data/botData.json
docker restart sdw-redeemer-bot
```



## Troubleshooting

### Commands not appearing in Discord

**Global sync (up to 1 hour):**
- Wait up to 1 hour for Discord to propagate commands globally
- Or kick & reinvite the bot

### Bot not responding

1. Check logs: `docker compose logs -f`
2. Verify `DISCORD_TOKEN` is correct
3. Ensure bot has proper permissions in Discord
4. Check that Message Content Intent is enabled

### Player nicknames not updating

Nicknames auto-update when:
- A gift code is successfully redeemed
- The game page returns a valid player name
- The stored name differs from the page name

## Building & Deploying Your Own Image

Use this workflow when you want to build from source, push to your own Docker Hub repository, and then pull and run on a remote server.

### 1. Log in to Docker Hub

```bash
docker login
# Enter your Docker Hub username and password when prompted
```

### 2. Build the Image

**Single-platform (current machine architecture):**

```bash
docker build -t brenak/kingshot-redeemer:latest .
```

**Multi-platform (AMD64 + ARM64) using buildx — recommended if deploying to a different architecture:**

```bash
# One-time setup: create a multi-platform builder (skip if already done)
docker buildx create --name multibuilder --use

# Build and push directly to Docker Hub in one step
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t brenak/kingshot-redeemer:latest \
  --push \
  .
```

### 3. Push to Docker Hub (single-platform only)

If you used the plain `docker build` above (not `buildx --push`), push the image separately:

```bash
docker push brenak/kingshot-redeemer:latest
```

### 4. Pull and Run on a Remote Server

SSH into your server, then:

```bash
docker pull brenak/kingshot-redeemer:latest
```

**Option A — Docker Compose (recommended):**

Copy your `docker-compose.yml` and `.env` file to the server, then:

```bash
docker compose up -d
docker compose logs -f   # verify it started cleanly
```

**Option B — Docker Run:**

```bash
docker run -d \
  --name sdw-redeemer-bot \
  --restart unless-stopped \
  -e DISCORD_TOKEN=your_discord_token_here \
  -v kingshot-data:/app/data \
  brenak/kingshot-redeemer:latest
```

### Updating to a New Build

On the remote server, pull the latest image and recreate the container (your volume data is preserved):

```bash
docker compose pull
docker compose up -d --force-recreate
```

Or with plain Docker:

```bash
docker pull brenak/kingshot-redeemer:latest
docker stop sdw-redeemer-bot && docker rm sdw-redeemer-bot
# re-run the docker run command above
```

## Supported Platforms

- ✅ `linux/amd64` (x86_64 - Most PCs and servers)
- ✅ `linux/arm64` (Apple Silicon Macs, Raspberry Pi, ARM servers)

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - feel free to use this project for personal or commercial purposes.

## Links

- [Docker Hub](https://hub.docker.com/r/jarecoder/kingshot-redeemer)
- [GitHub Repository](https://github.com/JareCoder/KingshotRedeemer)
- [Report Issues](https://github.com/JareCoder/KingshotRedeemer/issues)
