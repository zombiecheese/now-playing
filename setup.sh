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

echo "==> Setting up the Weather API..."
echo "Please enter your OpenWeatherMap API key:"
read -r openweathermap_api_key
echo "Enter your location coordinates in the 'latitude,longitude' format:"
read -r geo_coordinates

echo "==> Setting up the Spotify API..."
echo "Please enter your Spotify client ID:"
read -r spotify_client_id
echo "Please enter your Spotify client secret:"
read -r spotify_client_secret
echo "Please enter your Spotify playlist ID:"
read -r spotify_playlist_id

echo "Enter your OpenAI api key:"
read -r openai_api_key

echo "==> Setting up the configuration in config.yaml..."
echo "Select your Inky Impression display size:"
echo "1) 4.0 inch"
echo "2) 5.7 inch"
echo "3) 7.3 inch"
read -r -p "Enter choice (1/2/3): " display_size_choice

case $display_size_choice in
  1)
    display_width=640
    display_height=400
    album_cover_size=200
    ;;
  2)
    display_width=600
    display_height=448
    album_cover_size=250
    ;;
  3)
    display_width=800
    display_height=480
    album_cover_size=300
    ;;
  *)
    echo "Invalid choice. Defaulting to 5.7 inch settings."
    display_width=600
    display_height=448
    album_cover_size=250
    ;;
esac

echo "Select your Inky Impression display orientation:"
echo "1) portrait"
echo "2) landscape"


read -r -p "Enter choice (1/2/3): " display_size_choice

case $display_size_choice in
  1)
    orientation=portrait
    ;;
  2)
    orientation=landscape
    ;;
  *)
    echo "Invalid choice. Defaulting to portrait."
    orientation=portrait
    ;;
esac


cat <<EOF > "${install_path}/config/config.yaml"
display:
  # Physical hardware buffer for your Inky device
  width: $display_width
  height: $display_height

  # Fonts & layout
  font_path: "${install_path}/resources/CircularStd-Bold.otf"
  font_size_title: 45
  font_size_subtitle: 35
  offset_left_px: 20
  offset_right_px: 20
  offset_top_px: 0
  offset_bottom_px: 20
  offset_text_shadow_px: 4

  # Album Backdrop image styling
  backdrop_blur_radius: 12
  backdrop_darken_alpha: 120 
  backdrop_use_gradient: false
  small_album_cover_px: 480

  # Weather mode background (full-screen fit, orientation-aware, same location at ai generation)
  weather_background_image: "${install_path}/resources/ai_screensaver.png"

  # Global orientation (fallback if mode-specific values are not set)
  orientation: $orientation           # "portrait" or "landscape"
  
  # Rotation to align the portrait canvas to your hardware buffer
  # Change to 270 if your panel is mounted the other way.
  portrait_rotate_degrees: 90
  
  # Background fill behind square album art in portrait playing mode
  portrait_album_background_color: "black"

  # Text alignment per orientation
  # Valid values: "left" | "center" | "right"
  text_alignment_portrait: "center"
  text_alignment_landscape: "left"

  # Text wrapping options
  text_wrap_break_long_words: true   # break very long words if needed
  text_wrap_hyphenate: false         # add a hyphen at wrap if it fitss
  text_line_spacing_px: 4            # extra spacing between wrapped lines

weather:
  openweathermap_api_key: "${openweathermap_api_key}"
  geo_coordinates: "${geo_coordinates}"
  background_refresh_seconds: 900

spotify:
  client_id: "${spotify_client_id}"
  client_secret: "${spotify_client_secret}"
  playlist_id: ${spotify_playlist_id}

orchestrator:
  debounce_seconds: 30          # skip re-render if same track detected again within N seconds
  cache_ttl_seconds: 86400      # keep album/year enrichment in cache for 1 day
  cache_size: 512               # max enrichment entries
  cache_file_path: "${install_path}/cache/enrichment_cache.json"  # optional; omit to disable disk persistence

openai:
  api_key: $openai_api_key
  prompt_style: "80s anime" # enter image style type (default is 80s anime)
  model: "gpt-image-1"

image:
  orientation_strategy: "cover"   # "cover" or "contain"
  max_square_size: 1024           # used only for DALL-E 2 fallback

  fallback_image_path_day: "/home/zombiecheese/now-playing/resources/default_day.png"
  fallback_image_path_night: "/home/zombiecheese/now-playing/resources/default_night.png"
  fallback_image_path: "/home/zombiecheese/now-playing/resources/default.jpg"

lighting:
  day: "Use daytime lighting: natural brightness, appropriate color temperature, balanced contrast, and realistic shadows."
  twilight: "Use twilight lighting: soft low-angle light, gentle shadows, a sky gradient, moderate contrast, and selective artificial lights beginning to appear."
  night: "Render with low-light exposure: markedly darker scene, high contrast, cooler ambient tones, visible artificial lighting (street lamps, train interiors/headlights, illuminated windows), reduced sky luminance."

audio:
  recording_duration_seconds: 5 # Total duration to record for music detection/identification (max 10)

log:
  log_file_path: "${install_path}/log/now_playing.log" 

EOF
echo "✔ Configuration file created at ${install_path}/config/config.yaml."

echo "==> Setting up the now-playing systemd service..."
if [ -f "/etc/systemd/system/now-playing.service" ]; then
    echo "Removing old now-playing systemd service..."
    sudo systemctl stop now-playing
    sudo systemctl disable now-playing
    sudo rm -rf /etc/systemd/system/now-playing.*
    sudo systemctl daemon-reload
    echo "✔ Old now-playing systemd service removed."
fi
sudo cp "${install_path}/now-playing.service" /etc/systemd/system/
sudo sed -i -e "/\[Service\]/a ExecStart=${install_path}/venv/bin/python3 ${install_path}/src/now_playing.py" /etc/systemd/system/now-playing.service
sudo sed -i -e "/ExecStart/a WorkingDirectory=${install_path}" /etc/systemd/system/now-playing.service
sudo sed -i -e "/RestartSec/a User=$(id -u)" /etc/systemd/system/now-playing.service
sudo sed -i -e "/User/a Group=$(id -g)" /etc/systemd/system/now-playing.service

sudo systemctl daemon-reload
sudo systemctl start now-playing
sudo systemctl enable now-playing
echo "✔ now-playing systemd service installed and started."

echo "🎉 Setup is complete! Your now-playing display is configured."
