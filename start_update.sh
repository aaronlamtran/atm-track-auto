#!/usr/bin/env bash
set -e

git pull
sudo systemctl restart atm-mon
echo "Updated and restarted."
