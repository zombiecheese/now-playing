#!/bin/bash

if [[ $EUID -eq 0 ]]; then
  echo "Error: This script must NOT be run as root. Please run it as a regular user." >&2
  exit 1
fi

echo "==> Stopping now-playing systemd service..."
if [ -f "/etc/systemd/system/now-playing.service" ]; then
  sudo systemctl stop now-playing
fi
echo "==> fetching updated code from git"

git init
git remote remove origin
git remote add origin https://github.com/zombiecheese/now-playing
git fetch origin
git reset --hard origin/main

install_path=$(pwd)
source "${install_path}/venv/bin/activate" && echo "✔ Virtual environment activated."
echo "==> upgrading required Python packages..."
pip3 install -r requirements.txt --upgrade && echo "✔ Python packages installed successfully."
echo "Dectivating the virtual environment..."
source "${install_path}/venv/bin/deactivate" && echo "✔ Virtual environment activated."


echo "==> run systemctl start now-playing"
echo "🎉 Update is complete! Your now-playing display is configured."