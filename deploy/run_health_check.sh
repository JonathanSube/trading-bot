#!/usr/bin/env bash
# Ruft Claude Code headless auf, um den Signal-Bot zu pruefen und kleine
# Bugs selbst zu fixen - laeuft per systemd-Timer (signal-bot-check.timer)
# 3x taeglich, unabhaengig von einer offenen Chat-Session (die stirbt beim
# taeglichen 19-Uhr-Shutdown, siehe schedule_poweroff.py). --dangerously-
# skip-permissions ist hier noetig, weil niemand interaktiv Rueckfragen
# beantworten kann - bewusste Nutzerentscheidung (15.09.2026).
set -euo pipefail

REPO_DIR="/home/jonathan/programmieren/trading-bot"
cd "$REPO_DIR"

CLAUDE_BIN="$(ls -d "$HOME"/.config/Claude/claude-code/*/claude 2>/dev/null | sort -V | tail -1)"
if [ -z "$CLAUDE_BIN" ]; then
    echo "[health-check] claude-Binary nicht gefunden, breche ab." >&2
    exit 1
fi

"$CLAUDE_BIN" \
    --print \
    --model claude-sonnet-5 \
    --dangerously-skip-permissions \
    "$(cat "$REPO_DIR/deploy/health_check_prompt.txt")"
