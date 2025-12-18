#!/bin/bash

if [[ $EUID -eq 0 ]]; then
  echo "Error: This script must NOT be run as root. Please run it as a regular user." >&2
  exit 1
fi

if [ -d "now-playing" ]; then

echo "==> Stopping now-playing systemd service..."
if [ -f "/etc/systemd/system/now-playing.service" ]; then
    echo "Removing old now-playing systemd service..."
    sudo systemctl stop now-playing
fi
echo "==> fetching updated code from git"
cd now-playing 
git init
git remote add origin https://github.com/zombiecheese/now-playing
git fetch origin
git reset --hard origin/master
fi

cd now-playing || exit
install_path=$(pwd)

source "${install_path}/venv/bin/activate" && echo "✔ Virtual environment activated."


echo "==> upgrading required Python packages..."
pip3 install -r requirements.txt --upgrade && echo "✔ Python packages installed successfully."


echo "==> run systemctl start now-playing"

echo "🎉 Update is complete! Your now-playing display is configured."