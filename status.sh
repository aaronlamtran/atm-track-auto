#!/usr/bin/env bash
sudo systemctl status atm-mon
echo ""
echo "=== Live Logs (Ctrl+C to exit) ==="
journalctl -u atm-mon -f
