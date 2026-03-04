#!/usr/bin/env bash
sudo systemctl status atm-mon
echo ""
echo "=== Recent Logs ==="
journalctl -u atm-mon -n 20 --no-pager
