# Kingshot Redeemer Bot

A Discord bot that automates gift code redemption for Kingshot players using browser automation. Redeem codes for multiple accounts simultaneously with a single command.

# IMPORTANT Backup Your Data!
The migration in 1.1.0 update has a major oversight for docker volumes. Please backup your data NOW to avoid data loss.

```bash
# Export bot data from a running container
## Version 1.1.0
docker cp kingshot-redeemer:/data/botData.json ./botData.json

## Version 1.0.0
docker cp kingshot-redeemer:/app/data/players.json ./players.json
```

## Features

- 🎁 **Bulk Redemption** - Redeem gift codes for all registered players at once
- 🤖 **Browser Automation** - Uses Playwright for reliable web interaction
- 💾 **Auto-Sync Player Names** - Player nicknames automatically update from the offician redeeming site
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
  -e TIMEOUT_MS=500 \
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
      - TIMEOUT_MS=${TIMEOUT_MS}
    volumes:
      - kingshot-data:/data

volumes:
  kingshot-data:
```

2. Create a `.env` file:

```env
DISCORD_TOKEN=your_discord_token_here
TIMEOUT_MS=500
```

3. Start the bot:

```bash
docker-compose up -d
```

4. View logs:

```bash
docker-compose logs -f
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
| `/add <player_id>` | Add a new player by their Kingshot ID | `/add 123456789` |
| `/remove <query>` | Remove a player by ID or nickname | `/remove Jareggie` |
| `/list` | View all registered players (paginated, 10 per page) | `/list` |
| `/find <query>` | Search for a player by ID or nickname | `/find 123456789` |
| `/set-check-interval <hours>` | Set how often the bot checks for new gift codes (min 1 hour) | `/set-check-interval 2` |
| `/help` | Display all available commands and usage | `/help` |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | ✅ Yes | - | Your Discord bot token from the Developer Portal |
| `TIMEOUT_MS` | ❌ No | `500` | Browser automation timeout in milliseconds |
| `GIFT_CODE_CHECK_INTERVAL_HOURS` | ❌ No | `1` | How often (in hours) to check for new gift codes. Minimum 1. Can also be changed at runtime with `/set-check-interval` without redeploying. |

## Data Persistence

Bot data (including players and configuration) is stored in `/app/data/botData.json` inside the container. The Docker volume `kingshot-data` ensures your data persists across:
- Container restarts
- Bot updates
- System reboots



## Troubleshooting

### Commands not appearing in Discord

**Global sync (up to 1 hour):**
- Wait up to 1 hour for Discord to propagate commands globally
- Or kick & reinvite the bot

### Bot not responding

1. Check logs: `docker-compose logs -f`
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
docker-compose up -d
docker-compose logs -f   # verify it started cleanly
```

**Option B — Docker Run:**

```bash
docker run -d \
  --name sdw-redeemer-bot \
  --restart unless-stopped \
  -e DISCORD_TOKEN=your_discord_token_here \
  -e TIMEOUT_MS=500 \
  -v kingshot-data:/app/data \
  brenak/kingshot-redeemer:latest
```

### Updating to a New Build

On the remote server, pull the latest image and recreate the container (your volume data is preserved):

```bash
docker-compose pull
docker-compose up -d --force-recreate
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
