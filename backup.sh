#!/bin/bash
BACKUP_DIR="/home/ubuntu/sdw-redeemer/backup"
mkdir -p $BACKUP_DIR
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
docker cp sdw-redeemer-bot:/app/data/botData.json "$BACKUP_DIR/botData_$TIMESTAMP.json"
echo "Backed up to $BACKUP_DIR/botData_$TIMESTAMP.json"
