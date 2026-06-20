#!/bin/bash

if [[ $EUID -eq 0 ]]; then
  echo "Error: This script must NOT be run as root. Please run it as a regular user." >&2
  exit 1
fi

echo "==> Enabling SPI..."
sudo raspi-config nonint do_spi 0 && echo "✔ SPI is enabled."

echo "==> Enabling I2C..."
sudo raspi-config nonint do_i2c 0 && echo "✔ I2C is enabled."

echo "==> Updating package lists..."
sudo apt update && echo "✔ Package lists updated successfully."

echo "==> Upgrading system packages to the latest versions..."
sudo apt upgrade -y && echo "✔ System packages upgraded successfully."

echo "==> Installing required system dependencies..."
sudo apt-get install python3-numpy git libopenjp2-7 libportaudio2 -y \
  && echo "✔ System dependencies installed successfully."

if [ -d "now-playing" ]; then
    echo "==> Found an existing installation of now-playing. Removing it..."
    sudo rm -rf now-playing && echo "✔ Old installation removed."
fi

echo "==> Cloning the now-playing project from GitHub..."
git clone https://github.com/zombiecheese/now-playing && echo "✔ Project cloned successfully."
echo "Switching to the installation directory."
cd now-playing || exit
install_path=$(pwd)

echo "==> Setting up a Python virtual environment..."
python3 -m venv --system-site-packages venv && echo "✔ Python virtual environment created."
echo "Activating the virtual environment..."
source "${install_path}/venv/bin/activate" && echo "✔ Virtual environment activated."

echo "==> Upgrading pip in the virtual environment..."
pip install --upgrade pip && echo "✔ Pip upgraded successfully."

echo "==> Installing required Python packages..."
pip3 install -r requirements.txt --upgrade && echo "✔ Python packages installed successfully."

echo "==> Setting up configuration, resources and log directories..."
if ! [ -d "${install_path}/config" ]; then
    echo "Creating config directory..."
    mkdir -p "${install_path}/config" && echo "✔ Config directory created."
fi
if ! [ -d "${install_path}/resources" ]; then
    echo "Creating resources directory..."
    mkdir -p "${install_path}/resources" && echo "✔ Resources directory created."
fi
if ! [ -d "${install_path}/log" ]; then
    echo "Creating log directory..."
    mkdir -p "${install_path}/log" && echo "✔ Log directory created."
fi

echo "==> The web portal will initialize the SQLite settings store on first run."

echo "==> Setting up the now-playing systemd services..."
if [ -f "/etc/systemd/system/now-playing.service" ]; then
    echo "Removing old now-playing systemd service..."
    sudo systemctl stop now-playing
    sudo systemctl disable now-playing
    sudo rm -rf /etc/systemd/system/now-playing.*
    sudo systemctl daemon-reload
    echo "✔ Old now-playing systemd service removed."
fi
if [ -f "/etc/systemd/system/now-playing-web.service" ]; then
    echo "Removing old now-playing-web systemd service..."
    sudo systemctl stop now-playing-web
    sudo systemctl disable now-playing-web
    sudo rm -f /etc/systemd/system/now-playing-web.service
    sudo systemctl daemon-reload
    echo "✔ Old now-playing-web systemd service removed."
fi
sudo cp "${install_path}/now-playing.service" /etc/systemd/system/
sudo cp "${install_path}/now-playing-web.service" /etc/systemd/system/
sudo sed -i -e "/\[Service\]/a ExecStart=${install_path}/venv/bin/python3 ${install_path}/src/now_playing.py" /etc/systemd/system/now-playing.service
sudo sed -i -e "/ExecStart/a WorkingDirectory=${install_path}" /etc/systemd/system/now-playing.service
sudo sed -i -e "/RestartSec/a User=$(id -u)" /etc/systemd/system/now-playing.service
sudo sed -i -e "/User/a Group=$(id -g)" /etc/systemd/system/now-playing.service

sudo sed -i -e "/\[Service\]/a ExecStart=${install_path}/venv/bin/python3 ${install_path}/src/config_web_interface.py --host 0.0.0.0 --port 8088" /etc/systemd/system/now-playing-web.service
sudo sed -i -e "/ExecStart/a WorkingDirectory=${install_path}" /etc/systemd/system/now-playing-web.service
sudo sed -i -e "/RestartSec/a User=$(id -u)" /etc/systemd/system/now-playing-web.service
sudo sed -i -e "/User/a Group=$(id -g)" /etc/systemd/system/now-playing-web.service

SYSTEMCTL_BIN=$(command -v systemctl)
SUDOERS_FILE="/etc/sudoers.d/now-playing-web-restart"
echo "$(whoami) ALL=(root) NOPASSWD: ${SYSTEMCTL_BIN} restart now-playing.service, ${SYSTEMCTL_BIN} is-active now-playing.service" | sudo tee "${SUDOERS_FILE}" >/dev/null
sudo chmod 0440 "${SUDOERS_FILE}"
sudo visudo -cf "${SUDOERS_FILE}" >/dev/null && echo "✔ Sudoers permission configured for web-triggered app restarts."

sudo systemctl daemon-reload
sudo systemctl start now-playing
sudo systemctl enable now-playing
sudo systemctl start now-playing-web
sudo systemctl enable now-playing-web
echo "✔ now-playing and now-playing-web systemd services installed and started."

echo "🎉 Setup is complete! Your now-playing display is configured."
