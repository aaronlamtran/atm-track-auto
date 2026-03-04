#!/usr/bin/env bash
set -e

echo "=== ATM Monitor Setup ==="

# 1. Install Python dependencies
echo ""
echo "[1/3] Installing Python packages..."
pip3 install -r requirements.txt

# 2. Create .env if it doesn't exist
echo ""
echo "[2/3] Checking .env..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  Created .env from .env.example"
    echo "  *** Open .env and fill in your credentials before running. ***"
else
    echo "  .env already exists, skipping."
fi

# 3. Install or update Chromium + ChromeDriver (Raspberry Pi / Debian)
echo ""
echo "[3/3] Checking for Chromium..."
if ! command -v chromium-browser &>/dev/null && ! command -v chromium &>/dev/null; then
    echo "  Chromium not found. Installing (requires sudo)..."
    sudo apt-get update -qq
    sudo apt-get install -y chromium-browser chromium-chromedriver
    echo "  Chromium installed."
else
    # Show installed version
    INSTALLED=$(chromium-browser --version 2>/dev/null || chromium --version 2>/dev/null)
    echo "  Installed: $INSTALLED"

    # Check if an upgrade is available via apt
    sudo apt-get update -qq
    UPGRADABLE=$(apt-get --just-print upgrade 2>/dev/null \
        | grep -E "^Inst (chromium-browser|chromium) " || true)

    if [ -n "$UPGRADABLE" ]; then
        echo "  Update available — upgrading Chromium..."
        sudo apt-get install -y chromium-browser chromium-chromedriver
        echo "  Chromium updated: $(chromium-browser --version 2>/dev/null || chromium --version 2>/dev/null)"
    else
        echo "  Chromium is up to date."
    fi
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your credentials (if you haven't already)"
echo "  2. Run: python3 atm_mon.py"
echo ""
echo "To run in the background on boot (optional):"
echo "  crontab -e"
echo "  Add: @reboot cd $(pwd) && python3 atm_mon.py >> atm_mon.log 2>&1"
