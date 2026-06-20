#!/bin/bash

if [[ $EUID -eq 0 ]]; then
  echo "Error: This script must NOT be run as root. Please run it as a regular user." >&2
  exit 1
fi

echo "==> Stopping now-playing services..."
if [ -f "/etc/systemd/system/now-playing.service" ]; then
  sudo systemctl stop now-playing
fi
if [ -f "/etc/systemd/system/now-playing-web.service" ]; then
  sudo systemctl stop now-playing-web
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
echo "==> restarting now-playing service..."
sudo cp "${install_path}/now-playing.service" /etc/systemd/system/
if [ -f "${install_path}/now-playing-web.service" ]; then
  sudo cp "${install_path}/now-playing-web.service" /etc/systemd/system/
fi

# Rebuild service runtime fields from templates so unit files are always runnable.
sudo sed -i -e "/\[Service\]/a ExecStart=${install_path}/venv/bin/python3 ${install_path}/src/now_playing.py" /etc/systemd/system/now-playing.service
sudo sed -i -e "/ExecStart/a WorkingDirectory=${install_path}" /etc/systemd/system/now-playing.service
sudo sed -i -e "/RestartSec/a User=$(id -u)" /etc/systemd/system/now-playing.service
sudo sed -i -e "/User/a Group=$(id -g)" /etc/systemd/system/now-playing.service

if [ -f "/etc/systemd/system/now-playing-web.service" ]; then
  sudo sed -i -e "/\[Service\]/a ExecStart=${install_path}/venv/bin/python3 ${install_path}/src/config_web_interface.py --host 0.0.0.0 --port 8088" /etc/systemd/system/now-playing-web.service
  sudo sed -i -e "/ExecStart/a WorkingDirectory=${install_path}" /etc/systemd/system/now-playing-web.service
  sudo sed -i -e "/RestartSec/a User=$(id -u)" /etc/systemd/system/now-playing-web.service
  sudo sed -i -e "/User/a Group=$(id -g)" /etc/systemd/system/now-playing-web.service
fi

sudo systemctl daemon-reload
sudo systemctl restart now-playing
if [ -f "/etc/systemd/system/now-playing-web.service" ]; then
  sudo systemctl restart now-playing-web
fi
echo "🎉 Update is complete! Your now-playing display is configured."