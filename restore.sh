#!/bin/bash
# Restore botData.json into the running container.
# Usage:
#   ./restore.sh                          # restore the most recent backup
#   ./restore.sh botData_20260622_021500.json   # restore a specific file by name
#   ./restore.sh /full/path/to/file.json  # restore from an absolute path

BACKUP_DIR="/home/ubuntu/sdw-redeemer/backup"
CONTAINER="sdw-redeemer-bot"

if [ -z "$1" ]; then
    BACKUP_FILE=$(ls -t "$BACKUP_DIR"/botData_*.json 2>/dev/null | head -1)
    if [ -z "$BACKUP_FILE" ]; then
        echo "❌ No backups found in $BACKUP_DIR"
        exit 1
    fi
    echo "No file specified — using most recent backup:"
    echo "  $BACKUP_FILE"
else
    # Accept absolute path, bare filename, or filename relative to backup dir
    if [ -f "$1" ]; then
        BACKUP_FILE="$1"
    elif [ -f "$BACKUP_DIR/$1" ]; then
        BACKUP_FILE="$BACKUP_DIR/$1"
    else
        echo "❌ File not found: $1"
        echo "   Checked: $1"
        echo "   Checked: $BACKUP_DIR/$1"
        exit 1
    fi
fi

echo "Restoring from: $BACKUP_FILE"
docker cp "$BACKUP_FILE" "$CONTAINER:/app/data/botData.json"
if [ $? -ne 0 ]; then
    echo "❌ docker cp failed — is the container '$CONTAINER' running?"
    exit 1
fi

echo "Restarting container to apply restored data..."
docker restart "$CONTAINER"
echo "✅ Restore complete. Bot will reconnect in a few seconds."
