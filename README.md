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
| `/sheet sync [tab] [dry_run]` | Sync alliance roster sheet tab(s) against live MightPulse data — every configured tab if `tab` is omitted (see [Alliance sheet sync](#alliance-sheet-sync)) | `/sheet sync dry_run:True` |
| `/sheet add <kingdom> <tag> [tab]` | Sync an alliance into a sheet tab (tab defaults to the tag; re-adding a tab updates it) | `/sheet add 1343 2MK` |
| `/sheet analytics` | Refresh just the `Analytics` tab (one roster request per alliance) | `/sheet analytics` |
| `/sheet remove <tab>` / `/sheet list` | Stop syncing a tab / list alliance → tab targets | `/sheet list` |
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

| `GOOGLE_SHEETS_CREDENTIALS_PATH` | ❌ No | `/app/secrets/google-service-account.json` (compose) | Google service-account JSON key for `/sheet sync`. |
| `GOOGLE_SHEET_ID` | ❌ No | the alliance roster spreadsheet | Spreadsheet ID (between `/d/` and `/edit` in its URL). All tabs live in this one spreadsheet. |
| `SHEET_DEFAULT_KINGDOM` / `SHEET_DEFAULT_TAG` / `SHEET_DEFAULT_TAB` | ❌ No | `1343` / `1MK` / `1MK` | The first alliance → tab target. Only seeds the list the first time; after that it's managed with `/sheet add` / `/sheet remove` and stored in `botData.json`. Tag is case-sensitive. |
| `SHEET_AUTO_SYNC_HOURS` | ❌ No | `24` | Run a full `/sheet sync` (every tab + Analytics) automatically every N hours and post a summary to the bot channel. `0` turns it off. |

### MightPulse rate limits

The API key allows 60 requests/minute and 5,000/day, so the bot is deliberately frugal:

- The routine gift-code check makes **no** MightPulse calls; `/redeem` and `/catchup` don't either.
- When a new code appears, tracked alliance rosters are pulled first (one request per alliance covers every member), then only players outside those rosters are looked up individually. This happens before redeeming, so kingdom transfers are applied first.
- The daily alliance sync costs one request per tracked alliance.
- All requests share a throttle (~1 per 1.1s). A 429 is retried once; if it persists, the batch stops and the rest catch up next time.

## Alliance Sheet Sync

`/sheet sync` compares hand-maintained alliance roster tabs in a Google Sheet against live MightPulse data and writes the result back (ported from kingshot_web's SOS sheet card). Each tab tracks one alliance; which alliance goes in which tab is managed with `/sheet add <kingdom> <tag> [tab]` / `/sheet remove` / `/sheet list` (the first one is seeded from `SHEET_DEFAULT_*`, `1343` / `1MK` / tab `1MK`). `/sheet sync` with no `tab` syncs every tab in turn, looking each player up only once even if they appear on several tabs — so someone who moved from one tracked alliance to another shows 🟪 on the old tab and is appended to the new one.

For each row with a `Player ID`:

| Situation | Written | Row color (A → `observed_y`) |
|---|---|---|
| Unknown to MightPulse | `notes` = "not found" | unchanged |
| No longer on the alliance roster | (tag handling below) | 🟪 `#8e7cc3` |
| On the roster, position matches `x`/`y` | `observed_x`/`observed_y` cleared | 🟩 `#4ea72e` |
| On the roster, position differs | current position → `observed_x`/`observed_y` | 🟨 `#fbbc04` |
| On the roster, but MightPulse has no map position (inactive — not playing) | row moved below the active members | ⬜ `#b7b7b7` |

Two kinds of columns:

- **Live** — overwritten with the current value on every sync (only cells that actually changed are written):
  - the name column (`Current_Name`, else `Label/Name`/`Label`/`Name`)
  - `Kingdom` — so a server transfer shows where they went; the summary counts transfers
  - `Rank` (or `Alliance_Rank`) — `R1`–`R4` / `Leader`; for someone who left, their rank in their new alliance (blank if none)
  - `TC_Level` (or `TC`) — town center level as shown in game: 1–34 as-is, then 5 levels per True Gold tier (35 = `TG1`, 36–39 = `TG1.1`–`TG1.4`, 40 = `TG2`, … 54 = `TG4.4`, 55 = `TG5`)
  - `Power` — a real number (formatted `#,##0` on new tabs), so it sorts correctly
- **Recorded** — set when the row is added, then only changed by hand: `x`, `y` and `Current_Tag`. When the live value differs, it goes into `observed_x`/`observed_y`/`observed_tag`; when it matches again, those are cleared. The live tag is this alliance's tag while they're on its roster; otherwise their new tag, `none` (no alliance), or `left` (MightPulse hasn't caught up on where they went). Update the recorded value by hand once you've reviewed a change.

Roster members missing from the tab are appended with Player ID, name, `Original_Name`, the live columns, `Current_Tag`, and `x`/`y` set to their current state, so they start 🟩 with empty `observed_*` columns.

**Tabs can start blank.** If a tab doesn't exist, the sync creates it (only after the MightPulse data is in, so a mistyped tag doesn't leave an empty tab); if it's empty, it writes a header row — `Member #`, `Kingdom`, `Player ID`, `Original_Name`, `Current_Name`, `Current_Tag`, `Rank`, `TC_Level`, `Power`, `x`, `y`, `observed_tag`, `observed_x`, `observed_y`, `notes` — bolded, frozen and filterable, with the whole tab in Arial 11 — then adds every member.

`Member #` is a formula in the header cell, `={"Member #"; ARRAYFORMULA(IF(C2:C="", , ROW(C2:C)-1))}` (`C` = the `Player ID` column on new tabs; use whichever column it is on yours), that numbers every row with a Player ID 1…N from the top; it renumbers after sorting or filtering. To add it to an existing tab, put that in the header cell and leave the cells below it empty. Avoid something like `=SEQUENCE(100)` below the header: it fills rows that have no member, so the sync's new rows land after row 100. The sync never writes into cells it has no value for, so it can't break this or any other formula column. You can add your own columns after these; highlighting stops at `observed_y`, so they're never repainted. Existing tabs need `Player ID`, `Original_Name`, a name column (`Current_Name`, `Label/Name`, `Label` or `Name`), `x`, `y`, `observed_x`, `observed_y`, `observed_tag` and `notes`; `Kingdom`, `Current_Tag`, `Rank`, `TC_Level` and `Power` are optional — add any of those headers to an existing tab and the next sync fills them in (without `Current_Tag`, `observed_tag` is compared against the tab's alliance). Columns are found by header name, so order doesn't matter.

**Legend tab.** A `Legend` tab, kept second (right after `Analytics`), explains each row color and which columns are live vs. recorded. It's rewritten with the Analytics tab from the same color settings the sync uses, so it always matches.

**Inactive members to the bottom.** Rows colored ⬜ are moved below the active members on every sync (whole rows, so hand-kept columns and notes move with them), keeping their order among themselves; new active members are placed above them. Nothing moves once they're already at the bottom.

**Layout.** Every sync also fits all columns to their contents, centers `Current_Tag` and `observed_tag`, and left-aligns `Rank` and `TC_Level`, so existing tabs get the same layout as new ones.

**Daily auto-sync.** Once a day (`SHEET_AUTO_SYNC_HOURS`, default 24; `0` = off) the bot runs a full `/sheet sync` of every tab plus Analytics on its own, through the same queue as the commands, and posts a one-line-per-tab summary (🟩 matched · 🟨 moved · 🟪 left · ➕ added) to the bot channel. It's checked hourly and timed from the last run, which is saved, so restarts don't cause extra runs; `/sheet list` shows when it last ran. It costs the same MightPulse requests as a manual full sync, about 1.1s per member.

**Analytics tab.** After every real `/sheet sync` (not dry runs), and on `/sheet analytics`, the bot rewrites an `Analytics` tab comparing **all** configured alliances — even ones not synced in that run:

- a summary table, one row per alliance: Alliance, Kingdom, Members, TG5, TG4, TG3, TG2, TG1, Below TG1, Total Power, Avg Power, Top Power (TG buckets: TG5 = level 55+, TG4 = 50–54, TG3 = 45–49, TG2 = 40–44, TG1 = 35–39)
- a **TG5 pie** (share of TG5 players by alliance), a **total power bar chart**, and a **stacked bar of town center levels** per alliance

Alliances with under **5 billion** total power are left off the table and charts (a note under the table lists them). The `Analytics` tab is kept as the first tab in the spreadsheet.

It's built from each alliance's live roster (current members only), reusing the rosters the sync just fetched, so it usually costs no extra MightPulse requests — at most one per alliance. The charts are deleted and recreated on each update so they always match the table; edits to them (and anything typed into the tab) are overwritten. The alliance label is the tag, with the kingdom added when two tracked alliances share a tag. `Analytics` and `Legend` can't be used as alliance tab names.

**Cost/timing:** per tab, one roster request plus one player lookup per row and per new member, so ~2 minutes for ~100 rows at the MightPulse throttle. It runs in the command queue. Membership comes from the live roster; positions come from player lookups, which MightPulse may have cached for up to about a day. Nothing is written if any MightPulse request fails, or if rows were added/removed/re-sorted in the sheet while the sync ran.

**Setup:**

1. A Google Cloud service account with the **Google Sheets API** enabled, and a JSON key for it (the one kingshot_web used works as-is).
2. Share the spreadsheet with the key's `client_email` as **Editor**.
3. On the server, put the key at `secrets/google-service-account.json` next to `docker-compose.yml` (mounted read-only into the container), then `docker compose up -d`:
   ```bash
   mkdir -p secrets && chmod 700 secrets
   cp /path/to/key.json secrets/google-service-account.json && chmod 600 secrets/google-service-account.json
   ```
4. Run `/sheet sync dry_run:True` first — it does every read and comparison and surfaces a missing column, missing tab or sharing problem without writing.

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
