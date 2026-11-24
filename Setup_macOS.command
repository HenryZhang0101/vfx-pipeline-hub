#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

VENV_PY="$DIR/.venv_mac/bin/python3"

if [ ! -x "$VENV_PY" ]; then
    SYS_PY="$(command -v python3 || true)"
    if [ -z "$SYS_PY" ]; then
        echo "Error: python3 not found. Please install Python 3.11+ (e.g. brew install python)."
        read -r -p "Press Enter to exit..."
        exit 1
    fi
    echo "Creating macOS virtual environment (.venv_mac)..."
    "$SYS_PY" -m venv "$DIR/.venv_mac"
    "$VENV_PY" -m pip install --upgrade pip
    "$VENV_PY" -m pip install -r "$DIR/requirements.txt"
fi

"$VENV_PY" "$DIR/setup_client.py" "$@"

echo ""
read -r -p "Press Enter to close..."
