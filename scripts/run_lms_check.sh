#!/bin/bash
# Twice-daily LMS mail check, run by com.morriskight.la-council-tracker.lms.plist.
# Headless `claude -p` so each run is a fresh process with the current MCP tool
# list (the Gmail connector) -- an interactive session started before the
# connector existed never picks it up, see project_miki_citywatch_app.md.
set -euo pipefail
cd "$(dirname "$0")/.."

CLAUDE_BIN="/Users/morriskight/.local/bin/claude"
PROMPT_FILE="scripts/lms_fetch_prompt.md"
LOG="lms_check.log"

echo "=== LMS check run starting $(date) ===" >> "$LOG"
"$CLAUDE_BIN" -p "$(cat "$PROMPT_FILE")" >> "$LOG" 2>&1
echo "=== done $(date) ===" >> "$LOG"
