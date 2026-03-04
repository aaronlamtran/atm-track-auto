#!/usr/bin/env bash
set -e

echo "=== ATM Monitor Setup ==="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

# 1. Create venv if needed, then install dependencies
echo ""
echo "[1/3] Setting up Python environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  Created virtualenv at $VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
echo "  Packages installed."

# 2. Create .env if it doesn't exist
echo ""
echo "[2/3] Checking .env..."
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "  Created .env from .env.example"
    echo "  *** Open .env and fill in your credentials before running. ***"
else
    echo "  .env already exists, skipping."
fi

# 3. Set up systemd service (Linux / Raspberry Pi only)
echo ""
echo "[3/3] Setting up systemd service..."
if ! command -v systemctl &>/dev/null; then
    echo "  systemd not found (not Linux?) — skipping."
else
    PYTHON_BIN="$VENV_DIR/bin/python3"
    SERVICE_FILE="/etc/systemd/system/atm-mon.service"

    sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=ATM Balance Monitor
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_BIN $SCRIPT_DIR/atm_mon.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable atm-mon
    echo "  Service installed and enabled on boot."
    echo "  Start now with: sudo systemctl start atm-mon"
    echo "  View logs with: journalctl -u atm-mon -f"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your credentials (if you haven't already)"
echo "  2. sudo systemctl start atm-mon"
