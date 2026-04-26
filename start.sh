#!/bin/bash
# ─────────────────────────────────────────────
# AAT Bridge — Start
# ─────────────────────────────────────────────
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/venv/bin/python3"

if [ ! -f "$VENV" ]; then
    echo "❌ Virtual Environment nicht gefunden."
    echo "   Bitte zuerst: ./install.sh"
    exit 1
fi

if [ "$1" == "--setup" ]; then
    exec "$VENV" "$DIR/setup.py"
else
    exec "$VENV" "$DIR/aat_bridge.py"
fi
