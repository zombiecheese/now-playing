# 🎶 Now-playing

**Now-playing** is a Python application for the Raspberry Pi that listens for background music, identifies the
song, and displays the song information on an e-ink display.


Like many great Raspberry Pi projects, this one is a fork of a fork of a fork.
Huge thanks to the maintainers of:

- [spotipi-eink (original)](https://github.com/ryanwa18/spotipi-eink)
- [spotipi-eink (fork)](https://github.com/Gabbajoe/spotipi-eink)
- [shazampi-eink (fork)](https://github.com/ravi72munde/shazampi-eink)

Special shout out to maurocastermans:
- [now-playing (fork)](https://github.com/maurocastermans/now-playing)


All credits for the original idea go to them. This version builds on that foundation with AI image features, richer weather context, and a more complete admin experience.



## 🚀 Features

- Detects music using a
  local [YAMNet](https://www.kaggle.com/models/google/yamnet/tensorFlow2/yamnet/1?tfhub-redirect=true) ML model
- When music is detected, identifies the song with [ShazamIO](https://github.com/shazamio/ShazamIO)
- Displays song title, artist, and album cover on an e-ink display
- **Button A**: Toggles music detection and song lookup on/off
- **Button B**: Toggles AI background generation on/off (switches between generated backgrounds and static fallback images)
- **Button C**: Cycles through display orientations (portrait/landscape) and rotations
- **AI-Generated Backgrounds**: Uses OpenAI or Pixazo image generation to create weather-aware, time-of-day appropriate screensaver backgrounds
- **Orientation-Aware Fallback Controls**: The admin portal shows fallback image controls that match the selected orientation, keeping portrait and landscape tuning separate and clear
- **Fast Fallback Authoring**: "Use Current Generated" can copy the current generated image into day/night fallback slots (shown only while AI generation is enabled)
- **Admin Service Controls**: The web portal can show main app health and restart the main runtime service without taking down the portal
- When no music is detected for a while, the display switches to a screensaver mode that shows the weather with dynamic or static backgrounds

## 🎮 Button Controls

### Button A - Toggle Music Detection & Lookup
Press Button A to toggle the audio pipeline:
- **Enabled** (default): Microphone capture, music detection, and Shazam lookup run normally
- **Disabled**: Music detection/lookup pauses and the display remains in weather/screensaver flow
- This state is persisted and is reflected in the admin portal General settings

### Button B - Toggle AI Background Mode
Press Button B to toggle between AI-generated backgrounds and static fallback images for the screensaver:
- **AI Mode ON** (default): Generates unique weather-aware backgrounds based on current conditions, time of day, and your configured style
- **AI Mode OFF**: Uses static fallback images (day/night variants available)
- A small red dot appears on the screensaver when AI generation is disabled
- When re-enabling AI mode, a new background is immediately generated

### Button C - Cycle Display Orientation
Press Button C to cycle through all display orientation and rotation combinations:
1. Portrait (90°)
2. Portrait rotated (270°)
3. Landscape (0°)
4. Landscape rotated (180°)

The display immediately redraws with the new orientation, and your preference is saved across reboots.

## ✨ What's New?


### 🤖 AI Image Provider Integration

- **Dynamic Background Generation**: Uses an AI image provider (OpenAI or Pixazo) to create unique screensaver backgrounds
- **Weather-Aware**: Incorporates current weather conditions, temperature, and location into the generated imagery
- **Time-of-Day Adaptation**: Automatically adjusts lighting, color temperature, and scene elements based on:
  - Daytime: Natural brightness, realistic shadows, balanced contrast
  - Twilight: Soft low-angle light, gentle shadows, sky gradients
  - Night: Low-light exposure, high contrast, artificial lighting (street lamps, illuminated windows)
- **Astronomical Accuracy**: Calculates precise sun and moon positions to inform image generation
- **Customizable Style**: Configure your preferred artistic style (e.g., "80s anime", "cyberpunk", "impressionist painting")
- **Smart Caching**: Generated images are cached and refreshed on a configurable schedule (default: every 6 hours)
- **Fallback Support**: Automatically falls back to static images if API is unavailable or disabled
- **Provider Flexibility**: Supports OpenAI image models (DALL-E 2/3, GPT-Image variants) and Pixazo text-to-image generation
- **Orientation-Aware**: Generates images in the appropriate aspect ratio for your display orientation

### ♻️ Improvements

- Simplified, readable logic with meaningful function names
- Clear separation of concerns with dedicated services (e.g., `DisplayService` has all logic concerning the e-ink
  display)
- Application state is handled via a centralized `StateManager`
- Type hints added for better clarity and IDE support
- Configurations via the admin portal backed by SQLite (no more messy INI files)
- Cleaned up setup script for smoother installation
- Singleton pattern for `Logger` and `Config`
- Threaded button control for responsiveness
- Many more...

## 📦 Installation & Setup

### 🔧 Required Hardware

- [Raspberry Pi Zero 2 W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/) *(or newer)*
- [MicroSD card](https://www.raspberrypi.com/products/sd-cards/)
- [Power supply](https://www.raspberrypi.com/products/micro-usb-power-supply/)
- Pimoroni Inky Impression e-ink display
    - [Pimoroni Inky Impression 4"](https://shop.pimoroni.com/products/inky-impression-4?variant=39599238807635)
    - [Pimoroni Inky Impression 5.7"](https://shop.pimoroni.com/products/inky-impression-5-7?variant=32298701324371)
    - [Pimoroni Inky Impression 7.3"](https://shop.pimoroni.com/products/inky-impression-7-3?variant=55186435244411)
- [USB microphone](https://www.amazon.com.be/microphone-portable-enregistrement-vid%C3%A9oconf%C3%A9rences-n%C3%A9cessaire/dp/B09PVPPRF2?source=ps-sl-shoppingads-lpcontext&ref_=fplfs&ref_=fplfs&psc=1&smid=A3HYZLWFA5CWB0&gQT=1)
  *(min. 16kHz sample rate)*
- [USB-A to Micro-USB adapter](https://www.amazon.com.be/-/nl/Magnet-Adapter-Compatibel-Smartphones-randapparatuur/dp/B0CCSK6TWR/ref=sr_1_4?dib=eyJ2IjoiMSJ9.tSkQ7Eow3VuzOmbOparC3w6W72C_2lR7qR6GDXXFon_pZWGesfG0THfUPlsK47bxatu_2L-ennJAbfJOnxkvAT4PFFmsaLdhD5TxbF6-b5x0BBZ0cBfAzrGtuyrV64W2uwanSiruEmp4YzTr0veXeH0LK_YwEbmg6Cle6MP-_0hbOrEqdH83qKTqznjk0VJGjp1CmIb6v7-nMhO1tOFbc92DTz2RPYz207CHCzUXVuhVMyWsGMFb8oPqwCK_YbKaQtH0P0bKZqHN-uCreQRhWDefUiY6TUM6f6ryPNx2IaI.jD_UeNFvfX1JIecvwtP37jqDSlPx_A_PXUSiTBfzqCU&dib_tag=se&keywords=usb+a+to+micro+usb&qid=1752774830&s=electronics&sr=1-4)
  *(if your microphone is of type USB-A)*
- Optional: [3D printed case](https://github.com/scripsi/inky-impression-case)

### 🥧 Raspberry Pi OS

1. Flash Raspberry Pi OS Lite to your microSD card
   using [Raspberry Pi Imager](https://www.raspberrypi.com/documentation/computers/getting-started.html#installing-the-operating-system)
2. In the setup wizard, enable:
    - Wi-Fi
    - SSH — to allow remote access, as the OS is headless

### 🔐 Required Credentials

#### 🌦️ OpenWeatherMap API

1. Sign up at [OpenWeatherMap](https://openweathermap.org/)
2. Generate your API key
3. Store it, you will need it later

#### 📍Weather Coordinates

1. Go to [Google Maps](https://www.google.com/maps) → Search your location → Right-click → Copy coordinates
2. Store it, you will need it later

#### 🖼️ AI Provider API Key

Configure one provider in the admin portal:
- OpenAI: set your OpenAI API key
- Pixazo: set your Pixazo API key and preferred Pixazo model


### ⚙️ Installation Script

The installer is designed for a Raspberry Pi host and must be run as a regular user (not root).
It clones this repository into a `now-playing/` directory in your current location.

If an existing `now-playing/` directory is present, the setup script removes it first.
Back up local edits before running setup.

SSH into your Raspberry Pi:

```bash
  ssh <username>@<ip-address>
```

And run:

```bash
  wget https://raw.githubusercontent.com/zombiecheese/now-playing/main/setup.sh
  chmod +x setup.sh
  bash ./setup.sh
```

The `setup.sh` script installs and starts both systemd services:
- `now-playing.service` (main runtime)
- `now-playing-web.service` (admin portal)

It also injects runtime-specific unit fields (ExecStart, WorkingDirectory, User, Group)
into the copied service templates so the units are runnable on your host.

Verify both services start without errors:

```bash
  journalctl -u now-playing-web.service --follow
  journalctl -u now-playing.service --follow
```

Should you encounter any errors, check [Known Issues](#-known-issues)

> 🧙 <b>What the Script Does</b>
>
> - Enables SPI and I2C
> - Updates the system and installs dependencies
> - Sets up a Python virtual environment and installs Python packages
> - Creates config, log, and resources directories
> - Installs and starts both services (`now-playing.service` and `now-playing-web.service`)
> - Lets the admin portal initialize the SQLite settings store on first run
> - Configures systemd services to autostart on boot
> - Configures sudoers so the web service can restart/check the main service

> 📂 <b>Settings Store (SQLite)</b>
>
> The app now stores configuration in a disk-backed SQLite settings store that is initialized and edited through the admin portal.
> The portal exposes the same layout, image, weather, OpenAI, audio, logging, and orientation settings as form controls.
> Legacy YAML and JSON files are only used as one-time migration inputs when present.

## 🛠 Useful Commands

## ⚙️ Current Operations Model

The project now runs as two cooperating services:

- `now-playing.service`: main runtime (audio detection, song lookup, display rendering, AI/fallback decisions)
- `now-playing-web.service`: admin portal (settings UI, preview, events, cache stats, app status/restart controls)

The web service is intentionally separate so the portal can restart or inspect the main app without taking the portal down.

In normal operation, manage both services with systemd and use the web portal for day-to-day settings and runtime controls.

### 📝 Edit Configuration

To update your configuration after installation:

Open the admin portal and save changes there. The portal persists settings to the SQLite store and applies runtime toggle changes immediately.

### 🌐 Web Configuration Manager

You can manage the SQLite-backed settings store from a browser and preview the currently selected screensaver image.

The web manager runs as a dedicated `now-playing-web` systemd service, independent from the main `now-playing` runtime.
This allows the admin portal to restart the main app service without taking the portal down.

For standard usage, start/enable the service and access the portal in a browser.
Manual Python launch is only needed for debugging.

Run the manager:

```bash
  source venv/bin/activate
  python3 src/config_web_interface.py --host 0.0.0.0 --port 8088
```

Then open:

```text
  http://<your-pi-ip>:8088
```

What it supports:
- Structured editing of all supported settings through the admin form
- Automatic backups of the SQLite database before config saves
- Current display preview image including rendered overlays (song/weather text, orientation, AI indicator dot)
- Orientation-specific fallback image management (portrait/landscape and day/night)
- One-click copy of the current generated image into fallback slots when AI generation is enabled
- Main app service status checks and restart action from the portal

Notes:
- The portal persists changes directly to the database; no manual file editing is required
- The image preview is based on current fallback mode, orientation, and day/night assumptions
- In **Display & Image**, only fallback controls for the selected orientation are shown
- **Use Current Generated** is only shown while **Enable AI image generation** is enabled
- After restarting services, the web API can take a short moment to accept connections; refresh after a few seconds if needed

Recommended portal workflow:
1. Set runtime toggles in **General** (music detection, AI generation)
2. Choose orientation context in **Orientation-Specific** before adjusting offsets
3. Configure fallback images in **Display & Image** for portrait and landscape day/night variants
4. Use **Use Current Generated** to quickly seed fallback images from the current generated preview (AI enabled only)
5. Use **App Service** controls to verify health or restart the main runtime

### 🔄 Update Script

The `update.sh` script makes updating your installation simple and safe:

```bash
  bash update.sh
```

**What it does:**
- Stops both services
- Fetches the latest code from the GitHub repository
- Resets your installation to the latest version (preserves the SQLite settings database and cache files)
- Updates Python dependencies to their latest versions
- Reinstalls unit files, reapplies runtime unit fields (ExecStart/WorkingDirectory/User/Group), and restarts both services

**Important Notes:**
- Your SQLite settings database is preserved
- Must be run as a regular user (not root)
- Requires an active internet connection

### 🔁 Systemd Services

- Check status:

```bash
  sudo systemctl status now-playing.service
  sudo systemctl status now-playing-web.service
```

- Start/Stop:

```bash
  sudo systemctl stop now-playing.service
  sudo systemctl start now-playing.service
  sudo systemctl stop now-playing-web.service
  sudo systemctl start now-playing-web.service
```

- Logs:

```bash
  journalctl -u now-playing.service
  journalctl -u now-playing.service --follow
  journalctl -u now-playing.service --since today
  journalctl -u now-playing.service -b
  journalctl -u now-playing-web.service --follow
```

### 🧪 Manual Python Execution

Now-playing runs in a Python virtual environment (using venv). If you want to run the Python code manually:

```bash
  sudo systemctl stop now-playing.service
  source venv/bin/activate
  python3 src/now_playing.py
```

To leave the virtual environment:

```bash
  deactivate
```

## 🐛 Known Issues

### Low USB Microphone Gain

Some USB microphones have very low default input gain, meaning they only pick up sound when your audio device is
extremely close to the mic. This can cause issues with audio detection.

To boost your microphone’s gain:

1. Open the audio mixer:

```bash
    alsamixer
```

2. Select your USB microphone:
    1. Press F6 to open the sound card list
    2. Use the arrow keys to select your USB microphone device
3. Adjust the input gain:
    1. Press F4 to switch to Capture controls
    2. Increase the gain using the ↑ arrow key until it reaches an appropriate level
4. Save the gain settings (so they persist after reboot):

```bash
  sudo alsactl store
```

### GPIO Chip Conflict

If you see:

```
Woah there, some pins we need are in use!
     Chip Select: (line 8, GPIO8) currently claimed by spi0 CS0
```

Just recently (16/08/2024), the GPIO Kernel Module in Raspberry PI OS changed

➡️ Check https://github.com/pimoroni/inky?tab=readme-ov-file#chip-select-line-8-gpio8-currently-claimed-by-spi0-cs0 and
follow the instructions

## 🔮 What's Next?


### Button D

Button D is currently unused and could be mapped to additional features such as:
- Manual background refresh
- Cycling through different AI art styles
- Toggle between different weather data displays
- Screenshot/save current display

### Button 

Better handling of offsets during rotations!


### HTML Rendering

The Pimoroni Inky display actually
supports [rendering HTML](https://github.com/pimoroni/inky/tree/main/examples/7color/html), opening up all sorts of
design possibilities.
This could make the interface:

- More customizable and visually rich
- Easier to tweak via CSS/HTML templates
- Support dynamic layouts or themes

If you have more ideas for new features or you'd like to get involved, feel free to open an issue or submit a PR!


