#!/usr/bin/env bash
set -e

echo "=== ATM Monitor Setup ==="

# 1. Install Python dependencies
echo ""
echo "[1/2] Installing Python packages..."
pip3 install -r requirements.txt

# 2. Create .env if it doesn't exist
echo ""
echo "[2/2] Checking .env..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  Created .env from .env.example"
    echo "  *** Open .env and fill in your credentials before running. ***"
else
    echo "  .env already exists, skipping."
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
