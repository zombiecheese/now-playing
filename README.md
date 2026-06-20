# 🎶 Now-playing

**Now-playing** is a Python application for the Raspberry Pi that listens for background music, identifies the
song, and displays the song information on an e-ink display.


This, like any good project is a fork of a fork of a fork.
all thanks to the hard work of..

- [spotipi-eink (original)](https://github.com/ryanwa18/spotipi-eink)
- [spotipi-eink (fork)](https://github.com/Gabbajoe/spotipi-eink)
- [shazampi-eink (fork)](https://github.com/ravi72munde/shazampi-eink)

special shout out to maurocastermans - im even keeping 90% of your read me :P
- [now-playing (fork)](https://github.com/maurocastermans/now-playing)


All credits for the original idea go to them. While they laid the groundwork, this version focuses on dumb ai add ons and over engineered weather details 



## 🚀 Features

- Detects music using a
  local [YAMNet](https://www.kaggle.com/models/google/yamnet/tensorFlow2/yamnet/1?tfhub-redirect=true) ML model
- When music is detected, identifies the song with [ShazamIO](https://github.com/shazamio/ShazamIO)
- Displays song title, artist, and album cover on an e-ink display
- **Button A**: Toggles music detection and song lookup on/off
- **Button B**: Toggles AI background generation on/off (switches between generated backgrounds and static fallback images)
- **Button C**: Cycles through display orientations (portrait/landscape) and rotations
- **AI-Generated Backgrounds**: Uses OpenAI's image generation to create weather-aware, time-of-day appropriate screensaver backgrounds
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
- A small red dot appears on the screensaver when in fallback mode
- When re-enabling AI mode, a new background is immediately generated

### Button C - Cycle Display Orientation
Press Button C to cycle through all display orientation and rotation combinations:
1. Portrait (90°)
2. Portrait rotated (270°)
3. Landscape (0°)
4. Landscape rotated (180°)

The display immediately redraws with the new orientation, and your preference is saved across reboots.

## ✨ What's New?


### 🤖 OpenAI Integration

- **Dynamic Background Generation**: Uses OpenAI's image generation API to create unique screensaver backgrounds
- **Weather-Aware**: Incorporates current weather conditions, temperature, and location into the generated imagery
- **Time-of-Day Adaptation**: Automatically adjusts lighting, color temperature, and scene elements based on:
  - Daytime: Natural brightness, realistic shadows, balanced contrast
  - Twilight: Soft low-angle light, gentle shadows, sky gradients
  - Night: Low-light exposure, high contrast, artificial lighting (street lamps, illuminated windows)
- **Astronomical Accuracy**: Calculates precise sun and moon positions to inform image generation
- **Customizable Style**: Configure your preferred artistic style (e.g., "80s anime", "cyberpunk", "impressionist painting")
- **Smart Caching**: Generated images are cached and refreshed on a configurable schedule (default: every 6 hours)
- **Fallback Support**: Automatically falls back to static images if API is unavailable or disabled
- **Model Flexibility**: Supports multiple OpenAI image models (DALL-E 2/3, GPT-Image variants)
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

#### 🎵 Spotify API

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Click 'Create App' and fill out the form:
    1. App name
    2. App description
    3. Redirect URI = http://127.0.0.1:8888/callback
    4. Check 'Web API'
    5. Check the 'Terms of Service'
3. Click on 'Save'
4. Store your Client ID and Client Secret, you will need it later

#### 🎟 Spotify Access Token

Spotify authorization is now handled from the web admin portal:

1. Open the admin portal in your browser.
2. Enter your Spotify client ID and client secret in the portal.
3. Click **Open Spotify Login** in the Spotify Authorization section.
4. Complete the Spotify consent screen, then return to the portal and refresh the auth status.

The access token and refresh token are stored in the SQLite settings database, so there is no separate `.cache` file to copy around anymore.

### ⚙️ Installation Script

SSH into your Raspberry Pi:

```bash
  ssh <username>@<ip-address>
``

And run:

```bash
  wget https://raw.githubusercontent.com/zombiecheese/now-playing/main/setup.sh
  chmod +x setup.sh
  bash ./setup.sh
```

Spotipy will now automatically refresh the access token when it expires, using the refresh token stored in the database.

The `setup.sh` script will automatically start the now-playing systemd service. Verify that the service starts without
errors:

```bash
  journalctl -u now-playing.service --follow
```

Should you encounter any errors, check [Known Issues](#-known-issues)

> 🧙 <b>What the Script Does</b>
>
> - Enables SPI and I2C
> - Updates the system and installs dependencies
> - Sets up a Python virtual environment and installs Python packages
> - Creates config, log, and resources directories
> - Starts the service and lets the admin portal initialize the SQLite settings store
> - Copies and configures a systemd service to autostart on boot
> - Starts the now-playing service

> 📂 <b>Settings Store (SQLite)</b>
>
> The app now stores configuration in a disk-backed SQLite settings store that is initialized and edited through the admin portal.
> The portal exposes the same layout, image, weather, Spotify, OpenAI, audio, logging, and orientation settings as form controls.
> Legacy YAML and JSON files are only used as one-time migration inputs when present.

## 🛠 Useful Commands

### 📝 Edit Configuration

To update your configuration after installation:

Open the admin portal and save changes there. The portal persists settings to the SQLite store and applies runtime toggle changes immediately.

### 🌐 Web Configuration Manager

You can manage the SQLite-backed settings store from a browser and preview the currently selected screensaver image.

The web manager starts automatically when `now_playing.py` starts (including when run by systemd).

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

Notes:
- The portal persists changes directly to the database; no manual file editing is required
- The image preview is based on current fallback mode, orientation, and day/night assumptions

### � Update Script

The `update.sh` script makes updating your installation simple and safe:

```bash
  bash update.sh
```

**What it does:**
- Stops the now-playing service
- Fetches the latest code from the GitHub repository
- Resets your installation to the latest version (preserves the SQLite settings database and cache files)
- Updates Python dependencies to their latest versions
- Prompts you to restart the service

**Important Notes:**
- Your SQLite settings database is preserved
- Must be run as a regular user (not root)
- Requires an active internet connection
- After completion, manually restart the service:
  ```bash
  sudo systemctl start now-playing
  ```

### �🔁 Systemd Service

- Check status:

```bash
  sudo systemctl status now-playing.service
```

- Start/Stop:

```bash
  sudo systemctl stop now-playing.service
  sudo systemctl start now-playing.service
```

- Logs:

```bash
  journalctl -u now-playing.service
  journalctl -u now-playing.service --follow
  journalctl -u now-playing.service --since today
  journalctl -u now-playing.service -b
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

## 🎨 Fine-Tuning Display Layout

The admin portal exposes extensive control over text and image positioning, allowing you to perfectly align elements for your specific display and aesthetic preferences.

### Text Offset Configuration

Text offsets control the margins and shadow effects for song information. Configure separately for each orientation:

**Landscape Mode:**
```text
text_offset_left_px_landscape: 0      # Distance from left edge
text_offset_right_px_landscape: 0     # Distance from right edge
text_offset_top_px_landscape: 0       # Distance from top
text_offset_bottom_px_landscape: 0    # Distance from bottom
text_offset_text_shadow_px_landscape: 4  # Shadow depth for text readability
```

**Portrait Mode:**
```text
text_offset_left_px_portrait: 5       # Distance from left edge
text_offset_right_px_portrait: 20     # Distance from right edge
text_offset_top_px_portrait: 0        # Distance from top
text_offset_bottom_px_portrait: 80    # Distance from bottom
text_offset_text_shadow_px_portrait: 4   # Shadow depth for text readability
```

### Album Art Offset Configuration

Album art offsets allow precise positioning of the album cover image:

**Landscape Mode:**
```text
album_offset_left_px_landscape: 0     # Move album art left/right
album_offset_right_px_landscape: 0    # Adjust right-side spacing
album_offset_top_px_landscape: 0      # Move album art up/down
album_offset_bottom_px_landscape: 0   # Adjust bottom spacing
```

**Portrait Mode:**
```text
album_offset_left_px_portrait: 0      # Move album art left/right
album_offset_right_px_portrait: 14    # Adjust right-side spacing
album_offset_top_px_portrait: 49      # Move album art up/down
album_offset_bottom_px_portrait: 0    # Adjust bottom spacing
```

### Tuning Tips

1. **Start Small**: Make incremental changes (5-10px at a time) to avoid overshooting
2. **Test Both Orientations**: Remember to check both portrait and landscape modes
3. **Consider Text Length**: Longer song titles may need different offset values
4. **Shadow Depth**: Increase `text_offset_text_shadow_px` for better readability on busy backgrounds
5. **Live Testing**: After editing config, restart the service and wait for a song to play:
   ```bash
   sudo systemctl restart now-playing.service
   journalctl -u now-playing.service --follow
   ```

### Text Alignment Options

In addition to offsets, you can control text alignment:

```text
text_alignment_portrait: "center"    # Options: "left", "center", "right"
text_alignment_landscape: "left"     # Options: "left", "center", "right"
```

### Common Layout Scenarios

**Centered Layout (Portrait):**
```text
text_alignment_portrait: "center"
text_offset_left_px_portrait: 20
text_offset_right_px_portrait: 20
text_offset_bottom_px_portrait: 40
```

**Left-Aligned with Album on Right (Landscape):**
```text
text_alignment_landscape: "left"
text_offset_left_px_landscape: 20
album_offset_right_px_landscape: 20
```

**Bottom-Aligned Text (Portrait):**
```text
text_offset_bottom_px_portrait: 100   # Push text to bottom
album_offset_top_px_portrait: 20      # Album at top
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


