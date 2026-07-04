#!/bin/bash
# ─────────────────────────────────────────────
# UoPeople → Notion Sync — Cron Job Installer
# Sets up a daily cron job to run the sync script.
# ─────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_SCRIPT="$SCRIPT_DIR/sync.py"
LOG_DIR="$HOME/.uopeople-sync"
CRON_LOG="$LOG_DIR/cron.log"

# Default: Run every day at 7:00 AM
CRON_HOUR="${1:-7}"
CRON_MINUTE="${2:-0}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════════╗"
echo "║     📚 UoPeople Sync — Cron Job Setup           ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check if sync.py exists
if [ ! -f "$SYNC_SCRIPT" ]; then
    echo -e "${RED}❌ sync.py not found at: $SYNC_SCRIPT${NC}"
    exit 1
fi

# Find Python
PYTHON_BIN=$(which python3 2>/dev/null || which python 2>/dev/null)
if [ -z "$PYTHON_BIN" ]; then
    echo -e "${RED}❌ Python not found. Please install Python 3.8+${NC}"
    exit 1
fi

echo -e "  Python:     ${GREEN}$PYTHON_BIN${NC}"
echo -e "  Script:     ${GREEN}$SYNC_SCRIPT${NC}"
echo -e "  Schedule:   ${GREEN}Every day at ${CRON_HOUR}:$(printf '%02d' $CRON_MINUTE)${NC}"
echo -e "  Log file:   ${GREEN}$CRON_LOG${NC}"
echo ""

# Create log directory
mkdir -p "$LOG_DIR"

# Build the cron entry
CRON_ENTRY="$CRON_MINUTE $CRON_HOUR * * * cd $SCRIPT_DIR && $PYTHON_BIN $SYNC_SCRIPT >> $CRON_LOG 2>&1"

# Check if entry already exists
EXISTING=$(crontab -l 2>/dev/null | grep -F "sync.py" || true)

if [ -n "$EXISTING" ]; then
    echo -e "${YELLOW}⚠️  An existing cron entry was found:${NC}"
    echo "   $EXISTING"
    echo ""
    read -p "  Replace it? (y/N): " CONFIRM
    if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
        echo -e "${YELLOW}  Cancelled. No changes made.${NC}"
        exit 0
    fi
    # Remove old entry
    crontab -l 2>/dev/null | grep -vF "sync.py" | crontab -
fi

# Add the new cron entry
(crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -

echo -e "${GREEN}✅ Cron job installed successfully!${NC}"
echo ""
echo "  Your assignments will sync to Notion every day at ${CRON_HOUR}:$(printf '%02d' $CRON_MINUTE)"
echo ""
echo -e "  ${CYAN}Useful commands:${NC}"
echo "    View cron jobs:    crontab -l"
echo "    View sync log:     tail -f $CRON_LOG"
echo "    Remove cron job:   crontab -l | grep -v 'sync.py' | crontab -"
echo ""

# Optional: Change schedule
echo -e "  ${CYAN}To change the schedule:${NC}"
echo "    bash cron_setup.sh 8 30    # Run at 8:30 AM"
echo "    bash cron_setup.sh 22 0    # Run at 10:00 PM"
echo ""
