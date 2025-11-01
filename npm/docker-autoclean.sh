#!/bin/bash
# docker-autoclean.sh — conditional Docker cleanup when disk usage > 80%

LOGFILE="/var/log/docker-autoclean.log"
THRESHOLD=80             # percent full before cleaning
TARGET="/var/lib/docker" # directory to monitor (can change to / if docker uses root)
DATE="$(date '+%Y-%m-%d %H:%M:%S')"

# Get current usage (integer only)
USAGE=$(df -P "$TARGET" | awk 'NR==2 {print $5}' | tr -d '%')

# Log header
echo "[$DATE] Checking disk usage for $TARGET: ${USAGE}% full (threshold ${THRESHOLD}%)" >> "$LOGFILE"

if [ "$USAGE" -ge "$THRESHOLD" ]; then
  echo "[$DATE] Usage above threshold — starting cleanup..." >> "$LOGFILE"

  /usr/bin/docker system prune -af --volumes >> "$LOGFILE" 2>&1
  /usr/bin/docker builder prune -af >> "$LOGFILE" 2>&1

  echo "[$DATE] Cleanup complete." >> "$LOGFILE"
else
  echo "[$DATE] Usage below threshold — skipping cleanup." >> "$LOGFILE"
fi

echo "----------------------------------------" >> "$LOGFILE"
