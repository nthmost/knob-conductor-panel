#!/usr/bin/env bash
# Continuous deploy script for knob-conductor-panel on beyla
# Called by cron every minute — pulls latest main, restarts if changed
set -euo pipefail

REPO=/home/nthmost/projects/git/knob-conductor-panel
VENV=/home/nthmost/panel-env
SERVICE=knob-panel.service
LOGFILE=/home/nthmost/panel-deploy.log

cd "$REPO"

# Fetch latest
git fetch origin main --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

echo "$(date -Iseconds) Deploying $LOCAL -> $REMOTE" >> "$LOGFILE"

git pull --ff-only origin main >> "$LOGFILE" 2>&1

# Reinstall deps if requirements.txt changed
if git diff "$LOCAL" "$REMOTE" --name-only | grep -q requirements.txt; then
    echo "$(date -Iseconds) requirements.txt changed, installing deps" >> "$LOGFILE"
    "$VENV/bin/pip" install -r requirements.txt >> "$LOGFILE" 2>&1
fi

sudo systemctl restart "$SERVICE"
echo "$(date -Iseconds) Restarted $SERVICE (now $(git rev-parse --short HEAD))" >> "$LOGFILE"
