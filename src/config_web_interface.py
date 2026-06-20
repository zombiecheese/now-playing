import argparse
import base64
import binascii
import json
import mimetypes
import re
import shutil
import subprocess
import time
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit

from PIL import Image
import requests

from settings_store import SettingsStore
from service.ai_background_service import AIBackgroundService


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_STORE = SettingsStore()
MASKED_SECRET_VALUE = "********"
FALLBACK_UPLOAD_DIR = PROJECT_ROOT / "config" / "fallback_uploads"
TEST_AI_PREVIEW_PATH = PROJECT_ROOT / "config" / "test_ai_preview.png"
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
FALLBACK_TARGET_TO_CONFIG_KEY = {
  "fallback_day_portrait": "fallback_image_path_day_portrait",
  "fallback_night_portrait": "fallback_image_path_night_portrait",
  "fallback_day_landscape": "fallback_image_path_day_landscape",
  "fallback_night_landscape": "fallback_image_path_night_landscape",
}


def _sanitize_upload_stem(filename: str, default: str = "fallback") -> str:
  stem = Path(filename or "").stem.strip().lower()
  if not stem:
    return default
  cleaned = re.sub(r"[^a-z0-9_-]+", "-", stem).strip("-")
  return cleaned or default


def _resolve_upload_extension(filename: str) -> str:
  suffix = Path(filename or "").suffix.lower()
  if suffix in ALLOWED_IMAGE_EXTENSIONS:
    return suffix
  return ".png"


def _save_uploaded_fallback_image(target: str, filename: str, content_base64: str) -> str:
  if target not in {
    "fallback_legacy",
    "fallback_day_portrait",
    "fallback_night_portrait",
    "fallback_day_landscape",
    "fallback_night_landscape",
  }:
    raise ValueError("Unsupported upload target")

  if not isinstance(content_base64, str) or not content_base64.strip():
    raise ValueError("Missing upload content")

  try:
    raw = base64.b64decode(content_base64, validate=True)
  except (binascii.Error, ValueError) as exc:
    raise ValueError("Upload content is not valid base64") from exc

  if len(raw) > 15 * 1024 * 1024:
    raise ValueError("Upload exceeds 15 MB limit")

  try:
    with Image.open(BytesIO(raw)) as img:
      img.verify()
  except Exception as exc:
    raise ValueError("Uploaded file is not a valid image") from exc

  FALLBACK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
  stem = _sanitize_upload_stem(filename, default="fallback")
  ext = _resolve_upload_extension(filename)
  timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
  save_name = f"{target}_{timestamp}_{stem}{ext}"
  save_path = FALLBACK_UPLOAD_DIR / save_name
  save_path.write_bytes(raw)
  return save_path.relative_to(PROJECT_ROOT).as_posix()


def _resolve_project_relative_path(configured_path: str) -> Path:
  path_value = Path(str(configured_path))
  if path_value.is_absolute():
    return path_value
  return (PROJECT_ROOT / path_value).resolve()


def _resolve_current_generated_image(config: Dict[str, Any]) -> Path:
  # Prefer the rendered preview file because it reflects the current image selected by runtime logic.
  preview_path = resolve_rendered_preview_path(config)
  if preview_path.exists() and preview_path.is_file():
    return preview_path

  display_cfg = config.get("display", {}) if isinstance(config.get("display", {}), dict) else {}
  configured = str(display_cfg.get("weather_background_image", "") or "").strip()
  if configured:
    configured_path = _resolve_project_relative_path(configured)
    if configured_path.exists() and configured_path.is_file():
      return configured_path

  toggle_state = load_toggle_state()
  selected_path = resolve_current_image(config, toggle_state)
  if selected_path.exists() and selected_path.is_file():
    return selected_path

  raise ValueError("Current generated image path is empty. Generate an AI image first.")


def _save_current_generated_image_as_fallback(target: str, config: Dict[str, Any]) -> str:
  if target not in FALLBACK_TARGET_TO_CONFIG_KEY:
    raise ValueError("Unsupported fallback target.")

  source_path = _resolve_current_generated_image(config)
  if not source_path.exists() or not source_path.is_file():
    raise FileNotFoundError(f"Current generated image does not exist: {source_path}")

  try:
    with Image.open(source_path) as img:
      img.verify()
  except Exception as exc:
    raise ValueError("Current generated image is not a valid image file.") from exc

  FALLBACK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
  stem = _sanitize_upload_stem(source_path.name, default="generated")
  ext = source_path.suffix.lower() if source_path.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS else ".png"
  timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
  save_name = f"{target}_{timestamp}_{stem}{ext}"
  save_path = FALLBACK_UPLOAD_DIR / save_name
  shutil.copyfile(source_path, save_path)
  return save_path.relative_to(PROJECT_ROOT).as_posix()


def _extract_validation_error(response: requests.Response, fallback: str) -> str:
  try:
    payload = response.json()
  except ValueError:
    return fallback

  if isinstance(payload, dict):
    error_value = payload.get("error")
    if isinstance(error_value, dict):
      message = error_value.get("message") or error_value.get("type") or error_value.get("code")
      if isinstance(message, str) and message.strip():
        return message.strip()
    if isinstance(error_value, str) and error_value.strip():
      return error_value.strip()

    message = payload.get("message") or payload.get("error_description") or payload.get("detail")
    if isinstance(message, str) and message.strip():
      return message.strip()

  return fallback


def _validate_openai_api_key(api_key: str) -> tuple[bool, str]:
  try:
    response = requests.get(
      "https://api.openai.com/v1/models",
      headers={"Authorization": f"Bearer {api_key}"},
      timeout=5,
    )
  except requests.RequestException as exc:
    return False, f"OpenAI request failed: {exc}"

  if response.status_code == 200:
    return True, "OpenAI key accepted"

  return False, _extract_validation_error(response, f"OpenAI returned HTTP {response.status_code}")


def _validate_openweather_api_key(api_key: str) -> tuple[bool, str]:
  try:
    response = requests.get(
      "https://api.openweathermap.org/data/2.5/weather",
      params={"lat": "0", "lon": "0", "units": "metric", "appid": api_key},
      timeout=5,
    )
  except requests.RequestException as exc:
    return False, f"OpenWeather request failed: {exc}"

  if response.status_code == 200:
    return True, "OpenWeather key accepted"

  return False, _extract_validation_error(response, f"OpenWeather returned HTTP {response.status_code}")


def _validate_pixazo_api_key(api_key: str) -> tuple[bool, str]:
  if not api_key:
    return False, "Pixazo API key is required"

  try:
    response = requests.post(
      "https://gateway.pixazo.ai/flux-1-schnell/v1/getData",
      headers={
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "Ocp-Apim-Subscription-Key": api_key,
      },
      json={},
      timeout=10,
    )
  except requests.RequestException as exc:
    return False, f"Pixazo request failed: {exc}"

  if response.status_code in (200, 400):
    return True, "Pixazo key accepted"

  return False, _extract_validation_error(response, f"Pixazo returned HTTP {response.status_code}")


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Now Playing Admin Portal</title>
  <style>
    :root {
      --bg: #f7f1e6;
      --card: #fffdf8;
      --ink: #1e1e1a;
      --accent: #005f73;
      --accent-2: #bb3e03;
      --line: #d6d0c4;
      --ok: #2a9d8f;
      --err: #ae2012;
      --mono: "Cascadia Mono", Consolas, monospace;
      --sans: "Segoe UI", Tahoma, sans-serif;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: var(--sans);
      color: var(--ink);
      background:
        radial-gradient(circle at 10% 10%, #e9d8a6 0%, transparent 30%),
        radial-gradient(circle at 90% 20%, #94d2bd 0%, transparent 30%),
        linear-gradient(180deg, var(--bg), #efe9dc);
      min-height: 100vh;
    }

    .wrap {
      max-width: 1280px;
      margin: 24px auto;
      padding: 0 16px 24px;
      display: grid;
      grid-template-columns: 1.35fr 1fr;
      gap: 16px;
    }

    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08);
      overflow: hidden;
    }

    .card h2 {
      margin: 0;
      padding: 14px 16px;
      background: linear-gradient(90deg, #0a9396, #94d2bd);
      color: #fefefe;
      font-size: 1.05rem;
      letter-spacing: 0.02em;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }

    .card h2 .title-text {
      flex: 1;
    }

    .card h2 .title-buttons {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .card h2 button {
      padding: 6px 10px;
      font-size: 12px;
      background: rgba(255, 255, 255, 0.2);
      border: 1px solid rgba(255, 255, 255, 0.3);
      color: white;
      font-weight: 600;
    }

    .card h2 button:hover {
      background: rgba(255, 255, 255, 0.3);
    }

    .card h2 button.secondary {
      background: rgba(255, 255, 255, 0.15);
    }

    .card .content { padding: 14px 16px; }
    .card-wide { grid-column: 1 / -1; }

    .section-title {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin: 14px 0 8px;
      color: #5f5a50;
      font-weight: 800;
    }

    .subsection {
      border: 1px solid #e2dacb;
      border-radius: 12px;
      padding: 12px;
      background: #fffaf1;
      margin-bottom: 12px;
    }

    .subsection .section-title {
      margin-top: 0;
    }

    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--line);
    }

    .tab-button {
      border: 1px solid var(--line);
      background: #f4ede0;
      color: #453f36;
      padding: 8px 12px;
    }

    .tab-button.active {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }

    .tab-panel.hidden { display: none !important; }

    .grid {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .field {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .field label {
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.02em;
    }

    .field input,
    .field select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      font-family: var(--mono);
      font-size: 12px;
      background: #fff;
      color: #171717;
    }

    .field textarea {
      width: 100%;
      min-height: 96px;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      font-family: var(--mono);
      font-size: 12px;
      background: #fff;
      color: #171717;
      resize: vertical;
    }

    .checkbox-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 8px;
      font-size: 13px;
      font-weight: 600;
    }

    .row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 12px;
    }

    .hidden { display: none !important; }

    button {
      border: 0;
      border-radius: 10px;
      padding: 10px 14px;
      font-weight: 700;
      cursor: pointer;
      color: white;
      background: var(--accent);
    }

    button.secondary { background: var(--accent-2); }

    .status {
      margin-top: 10px;
      min-height: 1.2em;
      font-size: 0.95rem;
    }

    .status.ok { color: var(--ok); }
    .status.err { color: var(--err); }

    .meta {
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.5;
      background: #faf7ef;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .field-with-validation .input-with-validation {
      position: relative;
      display: flex;
      align-items: center;
      width: 100%;
    }

    .field-with-validation .input-with-validation input {
      padding-right: 40px;
    }

    .validation-indicator {
      position: absolute;
      right: 12px;
      top: 50%;
      transform: translateY(-50%);
      font-size: 16px;
      font-weight: 700;
      min-width: 20px;
      text-align: center;
      line-height: 1;
      pointer-events: none;
    }

    .validation-indicator.pending {
      color: #6b675f;
      font-size: 14px;
      animation: spin 1s linear infinite;
    }

    .validation-indicator.valid {
      color: var(--ok);
    }

    .validation-indicator.invalid {
      color: var(--err);
    }

    .validation-actions {
      display: flex;
      justify-content: flex-end;
      margin-top: 6px;
    }

    .field-upload {
      margin-top: 6px;
      display: flex;
      justify-content: flex-end;
    }

    .field-upload .secondary {
      font-size: 11px;
      padding: 6px 10px;
      border-radius: 8px;
    }

    .hidden-file-input {
      display: none;
    }

    .validation-test-btn {
      font-size: 11px;
      padding: 6px 10px;
      border-radius: 8px;
    }

    @keyframes spin {
      from { transform: translateY(-50%) rotate(0deg); }
      to { transform: translateY(-50%) rotate(360deg); }
    }

    .image-box {
      margin-top: 10px;
      border: 1px dashed #9a9488;
      border-radius: 12px;
      min-height: 220px;
      display: grid;
      place-items: center;
      background: repeating-linear-gradient(45deg, #f7f3e9, #f7f3e9 10px, #f0ebdf 10px, #f0ebdf 20px);
      overflow: hidden;
    }

    .image-box img {
      width: 100%;
      height: auto;
      display: block;
    }

    .placeholder {
      font-family: var(--mono);
      font-size: 12px;
      color: #6b675f;
      padding: 12px;
      text-align: center;
    }

    .event-meta {
      margin-top: 10px;
      font-family: var(--mono);
      font-size: 12px;
      color: #4a463f;
    }

    .event-log {
      margin-top: 10px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      max-height: 300px;
      overflow: auto;
      font-family: var(--mono);
      font-size: 12px;
    }

    .event-row {
      padding: 8px 10px;
      border-bottom: 1px solid #efe9dc;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .event-row:last-child { border-bottom: 0; }
    .event-row.failure { background: #fde8e8; color: #7f1d1d; border-left: 4px solid #b91c1c; }
    .event-row.fallback { background: #fff1db; color: #7c2d12; border-left: 4px solid #ea580c; }

    .debug-audio-meta {
      margin-top: 10px;
      font-family: var(--mono);
      font-size: 12px;
      color: #4a463f;
    }

    .debug-audio-list {
      margin-top: 10px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      max-height: 220px;
      overflow: auto;
      font-family: var(--mono);
      font-size: 12px;
    }

    .debug-audio-row {
      padding: 8px 10px;
      border-bottom: 1px solid #efe9dc;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
    }

    .debug-audio-row:last-child { border-bottom: 0; }

    .debug-audio-actions {
      display: flex;
      gap: 6px;
      justify-content: flex-end;
      flex-wrap: wrap;
    }

    .debug-audio-delete-btn {
      background: #b91c1c;
    }

    .debug-audio-name {
      font-weight: 600;
      word-break: break-all;
    }

    .debug-audio-details {
      color: #6b675f;
      margin-top: 2px;
    }

    .debug-audio-player {
      margin-top: 10px;
      width: 100%;
    }
    .event-row.info { background: #f8fafc; color: #1f2937; border-left: 4px solid #64748b; }

    @media (max-width: 980px) {
      .wrap { grid-template-columns: 1fr; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="card">
      <h2>
        <span class="title-text">Configuration Options</span>
        <div class="title-buttons">
          <button id="saveOptionsBtn">Save Options</button>
          <button class="secondary" id="reloadBtn">Reload</button>
        </div>
      </h2>
      <div class="content">
        <div class="section-title">General</div>
        <div class="grid">
          <div class="field"><label>Web Host</label><input id="webHost" /></div>
          <div class="field"><label>Web Port</label><input id="webPort" type="number" min="1" max="65535" /></div>
          <div class="field field-with-validation"><label>Admin API Token (leave blank to keep current)</label><div class="input-with-validation"><input id="webAdminToken" type="password" /><div class="validation-indicator" id="webAdminTokenValidation"></div></div></div>
        </div>

        <label class="checkbox-row"><input id="webEnabled" type="checkbox" /> Enable web interface</label>
        <label class="checkbox-row"><input id="musicDetectionEnabled" type="checkbox" /> Enable music detection and lookup</label>
        <label class="checkbox-row"><input id="openaiEnabled" type="checkbox" /> Enable AI image generation</label>

        <div id="aiDotMarginSettings" class="subsection hidden">
          <div class="section-title">AI Disabled Indicator Dot</div>
          <div class="grid">
            <div class="field"><label>AI Dot Margin X Px</label><input id="aiDotMarginXPx" type="number" min="0" /></div>
            <div class="field"><label>AI Dot Margin Y Px</label><input id="aiDotMarginYPx" type="number" min="0" /></div>
          </div>
        </div>

        <div class="section-title">Audio</div>
        <div class="grid">
          <div class="field"><label>Recording Duration Seconds</label><input id="audioDuration" type="number" min="1" max="30" /></div>
          <div class="field"><label>Audio Gain (dB)</label><input id="audioGainDb" type="range" min="-20" max="20" step="0.1" /></div>
        </div>

        <label class="checkbox-row"><input id="debugAudioEnabled" type="checkbox" /> Enable debug audio capture</label>

        <div id="debugAudioSettings" class="hidden">
          <div class="section-title">Debug Audio</div>
          <div class="grid">
            <div class="field"><label>Debug Audio Path</label><input id="debugAudioPath" /></div>
          </div>
          <div class="row">
            <button id="refreshDebugAudioBtn" type="button">Refresh Debug Audio</button>
            <button id="deleteAllDebugAudioBtn" type="button" class="debug-audio-delete-btn">Delete All Debug Audio</button>
          </div>
          <div id="debugAudioMeta" class="debug-audio-meta">Debug audio list unavailable.</div>
          <div id="debugAudioList" class="debug-audio-list"></div>
          <audio id="debugAudioPlayer" class="debug-audio-player" controls preload="none"></audio>
        </div>

        <div id="openaiSectionTitle" class="section-title hidden">AI Image Generation</div>
        <div id="openaiSettings" class="hidden">
          <div class="subsection">
            <div class="section-title">Provider</div>
            <div class="grid">
              <div class="field"><label>Image Provider</label><select id="aiProvider"><option value="openai">OpenAI</option><option value="pixazo">Pixazo (Free Tier)</option></select></div>
              <div class="field"><label>Prompt Style</label><input id="openaiPromptStyle" /></div>
            </div>
          </div>
          <div id="openaiProviderSettings" class="subsection">
            <div class="section-title">OpenAI Settings</div>
            <div class="grid">
              <div class="field"><label>OpenAI Model</label><input id="openaiModel" /></div>
              <div class="field field-with-validation"><label>OpenAI API Key (leave blank to keep current)</label><div class="input-with-validation"><input id="openaiApiKey" type="password" /><div class="validation-indicator" id="openaiApiKeyValidation"></div></div><div class="validation-actions"><button type="button" class="secondary validation-test-btn" id="testOpenaiKeyBtn">Test OpenAI Credentials</button></div></div>
            </div>
          </div>
          <div id="pixazoProviderSettings" class="subsection hidden">
            <div class="section-title">Pixazo Free Tier</div>
            <div class="grid">
              <div class="field"><label>Pixazo Free Model</label><select id="pixazoModel"><option value="flux-schnell">Flux 1 Schnell (Free)</option></select></div>
              <div class="field field-with-validation"><label>Pixazo API Key (leave blank to keep current)</label><div class="input-with-validation"><input id="pixazoApiKey" type="password" /><div class="validation-indicator" id="pixazoApiKeyValidation"></div></div><div class="validation-actions"><button type="button" class="secondary validation-test-btn" id="testPixazoKeyBtn">Test Pixazo Credentials</button></div></div>
            </div>
            <div class="meta">Pixazo uses its free Flux 1 Schnell endpoint here. The same provider selection is used for real runtime generation and test-image generation.</div>
          </div>
          <div class="subsection">
            <div class="section-title">Weather</div>
            <div class="grid">
              <div class="field"><label>Weather Refresh Seconds</label><input id="weatherRefresh" type="number" min="60" /></div>
              <div class="field"><label>Timezone</label><input id="weatherTimezone" /></div>
              <div class="field"><label>Weather Background Image</label><input id="weatherBg" /></div>
            </div>
          </div>
          <div class="subsection">
            <div class="section-title">Lighting Presets</div>
            <div class="grid">
              <div class="field"><label>Day Lighting</label><textarea id="lightingDay"></textarea></div>
              <div class="field"><label>Twilight Lighting</label><textarea id="lightingTwilight"></textarea></div>
              <div class="field"><label>Night Lighting</label><textarea id="lightingNight"></textarea></div>
            </div>
          </div>
          <div class="subsection">
            <div class="section-title">Prompt Preview</div>
            <div class="row"><button type="button" class="secondary" id="previewPromptBtn">Preview Current Prompt</button></div>
            <textarea id="promptPreview" class="meta" rows="5" readonly style="width:100%;margin-top:8px;resize:vertical;"></textarea>
          </div>
          <div class="subsection">
            <div class="section-title">Test Image Preview</div>
            <div class="row"><button type="button" class="secondary" id="testAiImageBtn">Generate Test Image</button></div>
            <div id="aiTestImageMeta" class="meta" style="margin-top:8px;">No test image generated yet.</div>
            <div id="aiTestImageBox" class="image-box"><div class="placeholder">Generate a test image to preview it here.</div></div>
          </div>
        </div>

        <div class="section-title">Display & Image</div>
        <div class="grid">
          <div class="field"><label>Display Width</label><input id="displayWidth" type="number" min="1" /></div>
          <div class="field"><label>Display Height</label><input id="displayHeight" type="number" min="1" /></div>
          <div class="field"><label>Font Path</label><input id="fontPath" /></div>
          <div class="field"><label>Font Fallback Path</label><input id="fontFallbackPath" /></div>
          <div class="field"><label>Font Size Title</label><input id="fontSizeTitle" type="number" min="1" /></div>
          <div class="field"><label>Font Size Subtitle</label><input id="fontSizeSubtitle" type="number" min="1" /></div>
          <div class="field"><label>Orientation Strategy</label><select id="orientationStrategy"><option value="cover">cover</option><option value="contain">contain</option></select></div>
          <div class="field"><label>Max Square Size</label><input id="maxSquareSize" type="number" min="1" /></div>
          <div class="field"><label>Legacy Fallback Image</label><input id="fallbackImagePath" /><div class="field-upload"><button type="button" class="secondary" id="uploadFallbackImageBtn">Upload</button></div></div>
          <div class="field orientation-portrait-only"><label>Fallback Day Portrait</label><input id="fallbackDayPortrait" /><div class="field-upload"><button type="button" class="secondary" id="uploadFallbackDayPortraitBtn">Upload</button><button type="button" class="secondary ai-enabled-only" id="setFallbackDayPortraitFromCurrentBtn">Use Current Generated</button></div></div>
          <div class="field orientation-portrait-only"><label>Fallback Night Portrait</label><input id="fallbackNightPortrait" /><div class="field-upload"><button type="button" class="secondary" id="uploadFallbackNightPortraitBtn">Upload</button><button type="button" class="secondary ai-enabled-only" id="setFallbackNightPortraitFromCurrentBtn">Use Current Generated</button></div></div>
          <div class="field orientation-landscape-only"><label>Fallback Day Landscape</label><input id="fallbackDayLandscape" /><div class="field-upload"><button type="button" class="secondary" id="uploadFallbackDayLandscapeBtn">Upload</button><button type="button" class="secondary ai-enabled-only" id="setFallbackDayLandscapeFromCurrentBtn">Use Current Generated</button></div></div>
          <div class="field orientation-landscape-only"><label>Fallback Night Landscape</label><input id="fallbackNightLandscape" /><div class="field-upload"><button type="button" class="secondary" id="uploadFallbackNightLandscapeBtn">Upload</button><button type="button" class="secondary ai-enabled-only" id="setFallbackNightLandscapeFromCurrentBtn">Use Current Generated</button></div></div>
          <div class="field"><label>Portrait Album Background Color</label><input id="portraitAlbumBackgroundColor" /></div>
          <div class="field"><label>Small Album Cover Px</label><input id="smallAlbumCoverPx" type="number" min="1" /></div>
          <div class="field"><label>Text Wrap Break Long Words</label><select id="textWrapBreakLongWords"><option value="true">true</option><option value="false">false</option></select></div>
          <div class="field"><label>Text Wrap Hyphenate</label><select id="textWrapHyphenate"><option value="true">true</option><option value="false">false</option></select></div>
          <div class="field"><label>Text Line Spacing Px</label><input id="textLineSpacingPx" type="number" min="0" /></div>
          <div class="field"><label>Backdrop Blur Radius</label><input id="backdropBlurRadius" type="number" min="0" /></div>
          <div class="field"><label>Backdrop Darken Alpha</label><input id="backdropDarkenAlpha" type="number" min="0" max="255" /></div>
          <div class="field"><label>Backdrop Use Gradient</label><select id="backdropUseGradient"><option value="true">true</option><option value="false">false</option></select></div>
        </div>

        <div class="section-title">Orientation-Specific</div>
        <div class="grid">
          <div class="field">
            <label>Selected Orientation</label>
            <select id="selectedOrientation"><option value="portrait">portrait</option><option value="landscape">landscape</option></select>
          </div>
        </div>

        <div id="portraitSettings" class="hidden">
          <div class="section-title">Portrait Offsets</div>
          <div class="grid">
            <div class="field"><label>Portrait Text Alignment</label><select id="portraitAlign"><option>left</option><option>center</option><option>right</option></select></div>
            <div class="field"><label>Text Offset Left Px</label><input id="portraitTextOffsetLeftPx" type="number" /></div>
            <div class="field"><label>Text Offset Right Px</label><input id="portraitTextOffsetRightPx" type="number" /></div>
            <div class="field"><label>Text Offset Top Px</label><input id="portraitTextOffsetTopPx" type="number" /></div>
            <div class="field"><label>Text Offset Bottom Px</label><input id="portraitTextOffsetBottomPx" type="number" /></div>
            <div class="field"><label>Text Shadow Px</label><input id="portraitTextShadowPx" type="number" /></div>
            <div class="field"><label>Album Offset Left Px</label><input id="portraitAlbumOffsetLeftPx" type="number" /></div>
            <div class="field"><label>Album Offset Right Px</label><input id="portraitAlbumOffsetRightPx" type="number" /></div>
            <div class="field"><label>Album Offset Top Px</label><input id="portraitAlbumOffsetTopPx" type="number" /></div>
            <div class="field"><label>Album Offset Bottom Px</label><input id="portraitAlbumOffsetBottomPx" type="number" /></div>
          </div>
        </div>

        <div id="landscapeSettings" class="hidden">
          <div class="section-title">Landscape Offsets</div>
          <div class="grid">
            <div class="field"><label>Landscape Text Alignment</label><select id="landscapeAlign"><option>left</option><option>center</option><option>right</option></select></div>
            <div class="field"><label>Text Offset Left Px</label><input id="landscapeTextOffsetLeftPx" type="number" /></div>
            <div class="field"><label>Text Offset Right Px</label><input id="landscapeTextOffsetRightPx" type="number" /></div>
            <div class="field"><label>Text Offset Top Px</label><input id="landscapeTextOffsetTopPx" type="number" /></div>
            <div class="field"><label>Text Offset Bottom Px</label><input id="landscapeTextOffsetBottomPx" type="number" /></div>
            <div class="field"><label>Text Shadow Px</label><input id="landscapeTextShadowPx" type="number" /></div>
            <div class="field"><label>Album Offset Left Px</label><input id="landscapeAlbumOffsetLeftPx" type="number" /></div>
            <div class="field"><label>Album Offset Right Px</label><input id="landscapeAlbumOffsetRightPx" type="number" /></div>
            <div class="field"><label>Album Offset Top Px</label><input id="landscapeAlbumOffsetTopPx" type="number" /></div>
            <div class="field"><label>Album Offset Bottom Px</label><input id="landscapeAlbumOffsetBottomPx" type="number" /></div>
          </div>
        </div>

        <div class="section-title">Weather & Integrations</div>
        <div class="grid">
          <div class="field field-with-validation"><label>OpenWeather API Key</label><div class="input-with-validation"><input id="weatherApiKey" type="password" /><div class="validation-indicator" id="weatherApiKeyValidation"></div></div><div class="validation-actions"><button type="button" class="secondary validation-test-btn" id="testWeatherKeyBtn">Test OpenWeather Credentials</button></div></div>
          <div class="field"><label>Geo Coordinates</label><input id="geoCoordinates" /></div>
        </div>

        <div class="section-title">Processing & Logs</div>
        <div class="grid">
          <div class="field"><label>Debounce Seconds</label><input id="debounceSeconds" type="number" min="0" /></div>
          <div class="field"><label>Cache TTL Seconds</label><input id="cacheTtlSeconds" type="number" min="0" /></div>
          <div class="field"><label>Cache Size</label><input id="cacheSize" type="number" min="0" /></div>
          <div class="field"><label>Log File Path</label><input id="logFilePath" /></div>
        </div>

        <div class="section-title">App Service</div>
        <div class="row">
          <button class="secondary" id="refreshAppStatusBtn" type="button">Refresh App Status</button>
          <button class="secondary" id="restartAppBtn" type="button">Restart App</button>
        </div>
        <div id="appServiceMeta" class="meta">Loading app service status...</div>

        <div id="status" class="status"></div>
      </div>
    </section>

    <section class="card">
      <h2>Current Selected Image</h2>
      <div class="content">
        <div id="meta" class="meta">Loading...</div>
        <div class="row"><button id="refreshImageBtn">Refresh Image State</button></div>
        <div id="imageBox" class="image-box"></div>
      </div>
    </section>

    <section class="card card-wide">
      <h2>Event Log</h2>
      <div class="content">
        <div class="row">
          <button id="refreshEventsBtn">Refresh Events</button>
          <button id="clearEventsBtn" class="debug-audio-delete-btn">Clear Events</button>
        </div>
        <div id="eventMeta" class="event-meta">Loading events...</div>
        <div id="eventLog" class="event-log"></div>
      </div>
    </section>

    <section class="card card-wide">
      <h2>Cache Health</h2>
      <div class="content">
        <div class="row"><button id="refreshCacheStatsBtn">Refresh Cache Stats</button></div>
        <div id="cacheStats" class="meta">Loading cache stats...</div>
      </div>
    </section>
  </div>

  <input type="file" id="uploadFallbackImageFile" class="hidden-file-input" accept="image/png,image/jpeg,image/webp,image/bmp" />
  <input type="file" id="uploadFallbackDayPortraitFile" class="hidden-file-input" accept="image/png,image/jpeg,image/webp,image/bmp" />
  <input type="file" id="uploadFallbackNightPortraitFile" class="hidden-file-input" accept="image/png,image/jpeg,image/webp,image/bmp" />
  <input type="file" id="uploadFallbackDayLandscapeFile" class="hidden-file-input" accept="image/png,image/jpeg,image/webp,image/bmp" />
  <input type="file" id="uploadFallbackNightLandscapeFile" class="hidden-file-input" accept="image/png,image/jpeg,image/webp,image/bmp" />

  <script>
    const statusEl = document.getElementById("status");
    const metaEl = document.getElementById("meta");
    const imageBox = document.getElementById("imageBox");
    const eventMetaEl = document.getElementById("eventMeta");
    const eventLogEl = document.getElementById("eventLog");
    const cacheStatsEl = document.getElementById("cacheStats");
    const appServiceMetaEl = document.getElementById("appServiceMeta");
    const aiTestImageMetaEl = document.getElementById("aiTestImageMeta");
    const aiTestImageBoxEl = document.getElementById("aiTestImageBox");
    const debugAudioMetaEl = document.getElementById("debugAudioMeta");
    const debugAudioListEl = document.getElementById("debugAudioList");
    const debugAudioPlayerEl = document.getElementById("debugAudioPlayer");

    const fields = {
      webEnabled: document.getElementById("webEnabled"),
      webHost: document.getElementById("webHost"),
      webPort: document.getElementById("webPort"),
      webAdminToken: document.getElementById("webAdminToken"),
      weatherRefresh: document.getElementById("weatherRefresh"),
      weatherTimezone: document.getElementById("weatherTimezone"),
      weatherBg: document.getElementById("weatherBg"),
      audioDuration: document.getElementById("audioDuration"),
      audioGainDb: document.getElementById("audioGainDb"),
      debugAudioEnabled: document.getElementById("debugAudioEnabled"),
      debugAudioPath: document.getElementById("debugAudioPath"),
      displayWidth: document.getElementById("displayWidth"),
      displayHeight: document.getElementById("displayHeight"),
      fontPath: document.getElementById("fontPath"),
      fontFallbackPath: document.getElementById("fontFallbackPath"),
      fontSizeTitle: document.getElementById("fontSizeTitle"),
      fontSizeSubtitle: document.getElementById("fontSizeSubtitle"),
      orientationStrategy: document.getElementById("orientationStrategy"),
      maxSquareSize: document.getElementById("maxSquareSize"),
      fallbackImagePath: document.getElementById("fallbackImagePath"),
      portraitAlbumBackgroundColor: document.getElementById("portraitAlbumBackgroundColor"),
      smallAlbumCoverPx: document.getElementById("smallAlbumCoverPx"),
      textWrapBreakLongWords: document.getElementById("textWrapBreakLongWords"),
      textWrapHyphenate: document.getElementById("textWrapHyphenate"),
      textLineSpacingPx: document.getElementById("textLineSpacingPx"),
      aiDotMarginXPx: document.getElementById("aiDotMarginXPx"),
      aiDotMarginYPx: document.getElementById("aiDotMarginYPx"),
      backdropBlurRadius: document.getElementById("backdropBlurRadius"),
      backdropDarkenAlpha: document.getElementById("backdropDarkenAlpha"),
      backdropUseGradient: document.getElementById("backdropUseGradient"),
      openaiEnabled: document.getElementById("openaiEnabled"),
      musicDetectionEnabled: document.getElementById("musicDetectionEnabled"),
      openaiModel: document.getElementById("openaiModel"),
      openaiPromptStyle: document.getElementById("openaiPromptStyle"),
      openaiApiKey: document.getElementById("openaiApiKey"),
      aiProvider: document.getElementById("aiProvider"),
      pixazoModel: document.getElementById("pixazoModel"),
      pixazoApiKey: document.getElementById("pixazoApiKey"),
      selectedOrientation: document.getElementById("selectedOrientation"),
      portraitAlign: document.getElementById("portraitAlign"),
      landscapeAlign: document.getElementById("landscapeAlign"),
      portraitTextOffsetLeftPx: document.getElementById("portraitTextOffsetLeftPx"),
      portraitTextOffsetRightPx: document.getElementById("portraitTextOffsetRightPx"),
      portraitTextOffsetTopPx: document.getElementById("portraitTextOffsetTopPx"),
      portraitTextOffsetBottomPx: document.getElementById("portraitTextOffsetBottomPx"),
      portraitTextShadowPx: document.getElementById("portraitTextShadowPx"),
      portraitAlbumOffsetLeftPx: document.getElementById("portraitAlbumOffsetLeftPx"),
      portraitAlbumOffsetRightPx: document.getElementById("portraitAlbumOffsetRightPx"),
      portraitAlbumOffsetTopPx: document.getElementById("portraitAlbumOffsetTopPx"),
      portraitAlbumOffsetBottomPx: document.getElementById("portraitAlbumOffsetBottomPx"),
      fallbackDayPortrait: document.getElementById("fallbackDayPortrait"),
      fallbackNightPortrait: document.getElementById("fallbackNightPortrait"),
      landscapeTextOffsetLeftPx: document.getElementById("landscapeTextOffsetLeftPx"),
      landscapeTextOffsetRightPx: document.getElementById("landscapeTextOffsetRightPx"),
      landscapeTextOffsetTopPx: document.getElementById("landscapeTextOffsetTopPx"),
      landscapeTextOffsetBottomPx: document.getElementById("landscapeTextOffsetBottomPx"),
      landscapeTextShadowPx: document.getElementById("landscapeTextShadowPx"),
      landscapeAlbumOffsetLeftPx: document.getElementById("landscapeAlbumOffsetLeftPx"),
      landscapeAlbumOffsetRightPx: document.getElementById("landscapeAlbumOffsetRightPx"),
      landscapeAlbumOffsetTopPx: document.getElementById("landscapeAlbumOffsetTopPx"),
      landscapeAlbumOffsetBottomPx: document.getElementById("landscapeAlbumOffsetBottomPx"),
      fallbackDayLandscape: document.getElementById("fallbackDayLandscape"),
      fallbackNightLandscape: document.getElementById("fallbackNightLandscape"),
      weatherApiKey: document.getElementById("weatherApiKey"),
      geoCoordinates: document.getElementById("geoCoordinates"),
      debounceSeconds: document.getElementById("debounceSeconds"),
      cacheTtlSeconds: document.getElementById("cacheTtlSeconds"),
      cacheSize: document.getElementById("cacheSize"),
      logFilePath: document.getElementById("logFilePath"),
      lightingDay: document.getElementById("lightingDay"),
      lightingTwilight: document.getElementById("lightingTwilight"),
      lightingNight: document.getElementById("lightingNight")
    };

    const openaiSettings = document.getElementById("openaiSettings");
    const openaiSectionTitle = document.getElementById("openaiSectionTitle");
    const aiDotMarginSettings = document.getElementById("aiDotMarginSettings");
    const debugAudioSettings = document.getElementById("debugAudioSettings");
    const portraitSettings = document.getElementById("portraitSettings");
    const landscapeSettings = document.getElementById("landscapeSettings");
    const MASKED_SECRET_VALUE = "********";
    const tabPanels = new Map();
    const tabButtons = new Map();
    let activateTabFn = null;
    let portalToken = "";
    let eventSource = null;
    let restartInFlight = false;

    try {
      portalToken = window.localStorage.getItem("nowPlayingAdminToken") || "";
    } catch (error) {
      void error;
      portalToken = "";
    }

    function updateStoredPortalToken() {
      try {
        if (portalToken) {
          window.localStorage.setItem("nowPlayingAdminToken", portalToken);
        } else {
          window.localStorage.removeItem("nowPlayingAdminToken");
        }
      } catch (error) {
        void error;
      }
    }

    function ensurePortalToken(forcePrompt = false) {
      if (!forcePrompt && portalToken) {
        return portalToken;
      }
      const entered = window.prompt("Admin token required for API access. Enter token:", portalToken || "");
      if (entered === null) {
        return portalToken;
      }
      portalToken = entered.trim();
      updateStoredPortalToken();
      return portalToken;
    }

    async function apiFetch(url, options = {}) {
      const headers = Object.assign({}, options.headers || {});
      if (portalToken) {
        headers["X-Admin-Token"] = portalToken;
      }

      let res = await fetch(url, Object.assign({}, options, { headers }));
      if (res.status !== 401) {
        return res;
      }

      ensurePortalToken(true);
      if (!portalToken) {
        return res;
      }

      const retryHeaders = Object.assign({}, headers, { "X-Admin-Token": portalToken });
      return fetch(url, Object.assign({}, options, { headers: retryHeaders }));
    }

    function setStatus(text, ok) {
      statusEl.textContent = text;
      statusEl.className = ok ? "status ok" : "status err";
    }

    function applyVisibilityRules() {
      const openaiEnabled = fields.openaiEnabled.checked;

      openaiSectionTitle.classList.toggle("hidden", !openaiEnabled);
      openaiSettings.classList.toggle("hidden", !openaiEnabled);

      const openaiTabButton = tabButtons.get("openai");
      const openaiTabPanel = tabPanels.get("openai");
      if (openaiTabButton) {
        openaiTabButton.classList.toggle("hidden", !openaiEnabled);
      }

      if (!openaiEnabled && openaiTabButton && openaiTabButton.classList.contains("active") && activateTabFn) {
        activateTabFn("general");
      }

      if (!openaiEnabled && openaiTabPanel) {
        openaiTabPanel.classList.add("hidden");
      }

      if (aiDotMarginSettings) {
        aiDotMarginSettings.classList.toggle("hidden", openaiEnabled);
      }
      document.querySelectorAll(".ai-enabled-only").forEach((el) => {
        el.classList.toggle("hidden", !openaiEnabled);
      });

      debugAudioSettings.classList.toggle("hidden", !fields.debugAudioEnabled.checked);
      const orientation = fields.selectedOrientation.value || "portrait";
      portraitSettings.classList.toggle("hidden", orientation !== "portrait");
      landscapeSettings.classList.toggle("hidden", orientation !== "landscape");
      document.querySelectorAll(".orientation-portrait-only").forEach((el) => {
        el.classList.toggle("hidden", orientation !== "portrait");
      });
      document.querySelectorAll(".orientation-landscape-only").forEach((el) => {
        el.classList.toggle("hidden", orientation !== "landscape");
      });

      const aiProvider = fields.aiProvider ? fields.aiProvider.value : "openai";
      const openaiProviderSettingsEl = document.getElementById("openaiProviderSettings");
      const pixazoProviderSettingsEl = document.getElementById("pixazoProviderSettings");
      if (openaiProviderSettingsEl) openaiProviderSettingsEl.classList.toggle("hidden", aiProvider !== "openai");
      if (pixazoProviderSettingsEl) pixazoProviderSettingsEl.classList.toggle("hidden", aiProvider !== "pixazo");
    }

    function renderAiTestImage(imageUrl, metaText) {
      if (aiTestImageMetaEl) {
        aiTestImageMetaEl.textContent = metaText || "";
      }
      if (!aiTestImageBoxEl) {
        return;
      }
      if (!imageUrl) {
        aiTestImageBoxEl.innerHTML = '<div class="placeholder">No test image available.</div>';
        return;
      }
      aiTestImageBoxEl.innerHTML = '<img alt="AI test image preview" src="' + imageUrl + '" />';
    }

    function buildApiErrorMessage(data, fallbackMessage) {
      if (!data || typeof data !== "object") {
        return fallbackMessage;
      }
      const parts = [];
      if (data.error) {
        parts.push(String(data.error));
      }
      if (data.reason) {
        parts.push(`Reason: ${data.reason}`);
      }
      if (data.hint) {
        parts.push(`Hint: ${data.hint}`);
      }
      if (data.provider) {
        parts.push(`Provider: ${data.provider}`);
      }
      if (data.error_type) {
        parts.push(`Type: ${data.error_type}`);
      }
      return parts.length ? parts.join(" ") : fallbackMessage;
    }

    function boolFromSelect(value) {
      return String(value).toLowerCase() === "true";
    }

    function setupSectionTabs() {
      const content = document.querySelector(".card .content");
      if (!content || content.dataset.tabsReady === "true") {
        return;
      }

      const sectionDefs = [
        { key: "general", title: "General" },
        { key: "audio", title: "Audio" },
        { key: "openai", title: "AI Image Generation" },
        { key: "display", title: "Display & Image" },
        { key: "orientation", title: "Orientation-Specific" },
        { key: "weather", title: "Weather & Integrations" },
        { key: "processing", title: "Processing & Logs" }
      ];

      const childNodes = Array.from(content.children);
      const titleNodes = new Map();
      for (const node of childNodes) {
        if (node.classList && node.classList.contains("section-title")) {
          titleNodes.set(node.textContent.trim(), node);
        }
      }

      const tabBar = document.createElement("div");
      tabBar.className = "tabs";
      content.insertBefore(tabBar, content.firstElementChild);

      function activateTab(key) {
        for (const [panelKey, panel] of tabPanels.entries()) {
          panel.classList.toggle("hidden", panelKey !== key);
        }
        for (const [buttonKey, button] of tabButtons.entries()) {
          button.classList.toggle("active", buttonKey === key);
        }
        try {
          window.localStorage.setItem("nowPlayingConfigTab", key);
        } catch (error) {
          void error;
        }
      }

      activateTabFn = activateTab;

      for (let index = 0; index < sectionDefs.length; index += 1) {
        const section = sectionDefs[index];
        const startNode = titleNodes.get(section.title);
        if (!startNode) {
          continue;
        }
        const endNode = index + 1 < sectionDefs.length ? titleNodes.get(sectionDefs[index + 1].title) : null;
        const panel = document.createElement("div");
        panel.className = "tab-panel";
        panel.dataset.tab = section.key;
        if (index !== 0) {
          panel.classList.add("hidden");
        }

        content.insertBefore(panel, startNode);
        let cursor = startNode;
        while (cursor && cursor !== endNode) {
          const nextNode = cursor.nextElementSibling;
          panel.appendChild(cursor);
          cursor = nextNode;
        }

        tabPanels.set(section.key, panel);

        const button = document.createElement("button");
        button.type = "button";
        button.className = "tab-button";
        button.textContent = section.title;
        button.addEventListener("click", () => activateTab(section.key));
        if (index === 0) {
          button.classList.add("active");
        }
        tabButtons.set(section.key, button);
        tabBar.appendChild(button);
      }

      const savedTab = (() => {
        try {
          return window.localStorage.getItem("nowPlayingConfigTab") || "general";
        } catch (error) {
          void error;
          return "general";
        }
      })();
      if (tabPanels.has(savedTab)) {
        activateTab(savedTab);
      }

      content.dataset.tabsReady = "true";
    }

    function applyOptionValues(data) {
      fields.webEnabled.checked = !!data.web_enabled;
      fields.webHost.value = data.web_host || "";
      fields.webPort.value = data.web_port ?? 8088;
      fields.webAdminToken.value = data.web_admin_token_configured ? MASKED_SECRET_VALUE : "";
      fields.weatherRefresh.value = data.weather_refresh_seconds ?? 3600;
      fields.weatherTimezone.value = data.weather_timezone || "";
      fields.weatherBg.value = data.display_weather_background || "";
      fields.audioDuration.value = data.audio_recording_duration_seconds ?? 5;
      fields.audioGainDb.value = data.audio_gain_db ?? 0;
      fields.debugAudioEnabled.checked = !!data.debug_audio_enabled;
      fields.debugAudioPath.value = data.debug_audio_path || "";
      fields.displayWidth.value = data.display_width ?? 800;
      fields.displayHeight.value = data.display_height ?? 480;
      fields.fontPath.value = data.font_path || "";
      fields.fontFallbackPath.value = data.font_fallback_path || "";
      fields.fontSizeTitle.value = data.font_size_title ?? 45;
      fields.fontSizeSubtitle.value = data.font_size_subtitle ?? 30;
      fields.orientationStrategy.value = data.orientation_strategy || "cover";
      fields.maxSquareSize.value = data.max_square_size ?? 1024;
      fields.fallbackImagePath.value = data.fallback_image_path || "";
      fields.portraitAlbumBackgroundColor.value = data.portrait_album_background_color || "black";
      fields.smallAlbumCoverPx.value = data.small_album_cover_px ?? 450;
      fields.textWrapBreakLongWords.value = String(!!data.text_wrap_break_long_words);
      fields.textWrapHyphenate.value = String(!!data.text_wrap_hyphenate);
      fields.textLineSpacingPx.value = data.text_line_spacing_px ?? 4;
      fields.aiDotMarginXPx.value = data.ai_dot_margin_x_px ?? 55;
      fields.aiDotMarginYPx.value = data.ai_dot_margin_y_px ?? 45;
      fields.backdropBlurRadius.value = data.backdrop_blur_radius ?? 12;
      fields.backdropDarkenAlpha.value = data.backdrop_darken_alpha ?? 120;
      fields.backdropUseGradient.value = String(!!data.backdrop_use_gradient);
      fields.openaiEnabled.checked = !!data.openai_enabled;
      fields.musicDetectionEnabled.checked = data.music_detection_enabled !== false;
      fields.openaiModel.value = data.openai_model || "";
      fields.openaiPromptStyle.value = data.openai_prompt_style || "";
      fields.openaiApiKey.value = data.openai_api_key_configured ? MASKED_SECRET_VALUE : "";
      if (fields.aiProvider) fields.aiProvider.value = data.ai_provider || "openai";
      if (fields.pixazoModel) fields.pixazoModel.value = data.pixazo_model || "flux-schnell";
      if (fields.pixazoApiKey) fields.pixazoApiKey.value = data.pixazo_api_key_configured ? MASKED_SECRET_VALUE : "";
      fields.selectedOrientation.value = data.selected_orientation || "portrait";
      fields.portraitAlign.value = data.text_alignment_portrait || "left";
      fields.landscapeAlign.value = data.text_alignment_landscape || "left";
      fields.portraitTextOffsetLeftPx.value = data.text_offset_left_px_portrait ?? 5;
      fields.portraitTextOffsetRightPx.value = data.text_offset_right_px_portrait ?? 20;
      fields.portraitTextOffsetTopPx.value = data.text_offset_top_px_portrait ?? 0;
      fields.portraitTextOffsetBottomPx.value = data.text_offset_bottom_px_portrait ?? 80;
      fields.portraitTextShadowPx.value = data.text_offset_text_shadow_px_portrait ?? 4;
      fields.portraitAlbumOffsetLeftPx.value = data.album_offset_left_px_portrait ?? 0;
      fields.portraitAlbumOffsetRightPx.value = data.album_offset_right_px_portrait ?? 14;
      fields.portraitAlbumOffsetTopPx.value = data.album_offset_top_px_portrait ?? 49;
      fields.portraitAlbumOffsetBottomPx.value = data.album_offset_bottom_px_portrait ?? 0;
      fields.fallbackDayPortrait.value = data.fallback_day_portrait || "";
      fields.fallbackNightPortrait.value = data.fallback_night_portrait || "";
      fields.landscapeTextOffsetLeftPx.value = data.text_offset_left_px_landscape ?? 0;
      fields.landscapeTextOffsetRightPx.value = data.text_offset_right_px_landscape ?? 0;
      fields.landscapeTextOffsetTopPx.value = data.text_offset_top_px_landscape ?? 0;
      fields.landscapeTextOffsetBottomPx.value = data.text_offset_bottom_px_landscape ?? 0;
      fields.landscapeTextShadowPx.value = data.text_offset_text_shadow_px_landscape ?? 4;
      fields.landscapeAlbumOffsetLeftPx.value = data.album_offset_left_px_landscape ?? 0;
      fields.landscapeAlbumOffsetRightPx.value = data.album_offset_right_px_landscape ?? 0;
      fields.landscapeAlbumOffsetTopPx.value = data.album_offset_top_px_landscape ?? 0;
      fields.landscapeAlbumOffsetBottomPx.value = data.album_offset_bottom_px_landscape ?? 0;
      fields.fallbackDayLandscape.value = data.fallback_day_landscape || "";
      fields.fallbackNightLandscape.value = data.fallback_night_landscape || "";
      fields.weatherApiKey.value = data.weather_api_key_configured ? MASKED_SECRET_VALUE : "";
      fields.geoCoordinates.value = data.geo_coordinates || "";
      fields.debounceSeconds.value = data.debounce_seconds ?? 30;
      fields.cacheTtlSeconds.value = data.cache_ttl_seconds ?? 86400;
      fields.cacheSize.value = data.cache_size ?? 512;
      fields.logFilePath.value = data.log_file_path || "";
      fields.lightingDay.value = data.lighting_day || "";
      fields.lightingTwilight.value = data.lighting_twilight || "";
      fields.lightingNight.value = data.lighting_night || "";
      applyVisibilityRules();
    }

    function applyHoverHints() {
      const fieldHints = {
        "Web Host": "IP/interface to bind the admin server. Use 0.0.0.0 for LAN access, or 127.0.0.1 for local-only.",
        "Web Port": "HTTP port for the admin portal. Example: 8088. Change if another service already uses this port.",
        "Admin API Token (leave blank to keep current)": "Optional API protection token. Set once, then clients must send it for /api routes.",
        "Recording Duration Seconds": "How long each microphone capture window is before detection/identify. Example: 5s is responsive; 8-10s can improve recognition in noisy rooms.",
        "Audio Gain (dB)": "Applies digital gain before analysis. Increase if captures are quiet; reduce if clipping/noise increases. Typical range: -5 to +10 dB.",
        "Debug Audio Path": "Folder to save captured debug clips when debug capture is enabled. Example: debug_audio.",
        "Image Provider": "Choose which provider settings to use for AI image generation. Pixazo is currently limited to intended free-tier model choices.",
        "OpenAI Model": "Image model used for AI background generation. Example: gpt-image-1-mini for lower cost/faster generation.",
        "Prompt Style": "Creative style appended to prompts. Example: moody watercolor cityscape, retro synthwave sunset, minimalist ink wash.",
        "OpenAI API Key (leave blank to keep current)": "Secret key for OpenAI requests. Leave as ******** to keep current value, or paste a new key to rotate.",
        "Pixazo Free Model": "Select from the intended free-tier Pixazo model options.",
        "Pixazo API Key (leave blank to keep current)": "Pixazo subscription key sent as Ocp-Apim-Subscription-Key. Leave as ******** to keep the current value.",
        "Weather Refresh Seconds": "How often weather context and AI background refresh logic run. Example: 3600 = once per hour.",
        "Timezone": "Timezone used for day/twilight/night decisions. Example: Australia/Melbourne or Europe/London.",
        "Weather Background Image": "Path to the fallback/active weather background image file used when no generated image is selected.",
        "AI Dot Margin X Px": "Horizontal offset of the small AI/fallback indicator dot from the display edge in pixels.",
        "AI Dot Margin Y Px": "Vertical offset of the small AI/fallback indicator dot from the display edge in pixels.",
        "Display Width": "Target canvas width in pixels for composition. Example: 800 for Inky Impression 5.7 landscape.",
        "Display Height": "Target canvas height in pixels for composition. Example: 480 for Inky Impression 5.7 landscape.",
        "Font Path": "Primary font file path used for title/subtitle rendering.",
        "Font Fallback Path": "Fallback font (for missing glyphs/CJK). Used when the primary font lacks required characters.",
        "Font Size Title": "Primary song/weather headline text size in pixels. Increase for emphasis, decrease to avoid wrapping.",
        "Font Size Subtitle": "Secondary metadata text size in pixels, usually artist/description line.",
        "Orientation Strategy": "How images fit the display: cover fills and crops edges; contain preserves full image with possible letterboxing.",
        "Max Square Size": "Maximum generated square image dimension. Higher values can improve detail but increase latency/cost.",
        "Legacy Fallback Image": "Single generic fallback background path used when orientation/day-night-specific files are unavailable.",
        "Portrait Album Background Color": "Solid fallback color behind portrait album art when no backdrop image is available.",
        "Small Album Cover Px": "Album cover render size in pixels in layouts that use reduced cover artwork.",
        "Text Wrap Break Long Words": "If true, very long words can break mid-word to avoid overflow beyond text area.",
        "Text Wrap Hyphenate": "If true, wrapping may add hyphenation where supported for cleaner long-word line breaks.",
        "Text Line Spacing Px": "Extra vertical spacing between text lines. Example: 2-6 for readability tuning.",
        "Backdrop Blur Radius": "Blur strength applied to backdrop image. Higher radius = softer, less distracting background.",
        "Backdrop Darken Alpha": "Dark overlay opacity (0-255) over backdrop. Higher values improve text contrast.",
        "Backdrop Use Gradient": "Use gradient darkening instead of flat overlay to preserve more visual depth.",
        "Selected Orientation": "Preview/edit mode for orientation-specific settings. Choose portrait or landscape before adjusting offsets.",
        "OpenWeather API Key": "API key for weather lookups. Leave as ******** to keep current value, or paste a new key.",
        "Geo Coordinates": "Latitude,longitude for weather and lighting context. Example: -37.8136,144.9631.",
        "Debounce Seconds": "Minimum time between repeated expensive operations to prevent rapid re-triggering.",
        "Cache TTL Seconds": "How long cached enrichment data remains valid. Example: 86400 = 24 hours.",
        "Cache Size": "Maximum number of cached enrichment records before old entries are evicted.",
        "Log File Path": "Path to the runtime log file used by event feed and troubleshooting views.",
        "Day Lighting": "Prompt guidance used for daytime generated scenes.",
        "Twilight Lighting": "Prompt guidance used around sunrise/sunset transitions.",
        "Night Lighting": "Prompt guidance used for low-light generated scenes.",
      };

      const checkboxHints = {
        "Enable web interface": "Turns the admin HTTP portal on or off.",
        "Enable music detection and lookup": "Controls whether microphone capture, music detection, and song identification run. Disable to keep screensaver/weather only.",
        "Enable AI image generation": "Enables AI image generation; when off, the app uses configured static fallback images.",
        "Enable debug audio capture": "Saves captured audio clips for troubleshooting detection/identify behavior.",
      };

      const buttonHints = {
        "Save Options": "Persists all current settings to the database.",
        "Reload": "Re-reads settings from the database and refreshes the form.",
        "Restart App": "Restarts the main now-playing runtime service without taking down this admin panel.",
        "Refresh App Status": "Queries the main now-playing service state from systemd.",
        "Upload": "Uploads an image file and writes its path into this setting.",
        "Use Current Generated": "Copies the current generated weather image into this fallback slot and saves it immediately.",
        "Preview Current Prompt": "Preview the AI image prompt built from the current form values.",
        "Generate Test Image": "Generate a non-live test image using the current AI settings and preview it below.",
        "Refresh Image State": "Fetches the latest selected/preview image metadata and updates preview pane.",
        "Refresh Events": "Loads recent service log events.",
        "Clear Events": "Clears the current event log file content.",
        "Refresh Debug Audio": "Loads recent debug audio recordings from the configured debug audio folder.",
        "Delete All Debug Audio": "Deletes all debug recordings from the configured debug audio folder.",
        "Refresh Cache Stats": "Reloads cache/database health statistics.",
      };

      const makeFallbackHint = (labelText, control) => {
        const type = (control.getAttribute('type') || '').toLowerCase();
        if (type === 'number' || type === 'range') {
          return `Adjusts ${labelText.toLowerCase()} numerically. Smaller values usually reduce effect; larger values increase it.`;
        }
        if (control.tagName === 'SELECT') {
          return `Chooses how ${labelText.toLowerCase()} behaves.`;
        }
        if (control.tagName === 'TEXTAREA') {
          return `Free-text configuration for ${labelText.toLowerCase()}.`;
        }
        return `Configuration value for ${labelText.toLowerCase()}.`;
      };

      const fieldNodes = document.querySelectorAll('.field');
      for (const fieldNode of fieldNodes) {
        const label = fieldNode.querySelector('label');
        const control = fieldNode.querySelector('input, select, textarea');
        if (!label || !control) {
          continue;
        }

        const labelText = (label.textContent || '').trim();
        const hint = fieldHints[labelText] || makeFallbackHint(labelText, control);
        if (!label.title) {
          label.title = hint;
        }
        if (!control.title) {
          control.title = hint;
        }
      }

      const checkboxRows = document.querySelectorAll('.checkbox-row');
      for (const row of checkboxRows) {
        const rowText = (row.textContent || '').trim();
        const hint = checkboxHints[rowText] || `Enables or disables: ${rowText.toLowerCase()}.`;
        if (!row.title) {
          row.title = hint;
        }
        const checkbox = row.querySelector('input[type="checkbox"]');
        if (checkbox && !checkbox.title) {
          checkbox.title = hint;
        }
      }

      const buttons = document.querySelectorAll('button');
      for (const button of buttons) {
        const text = (button.textContent || '').trim();
        if (!button.title) {
          button.title = buttonHints[text] || text;
        }
      }
    }

    function formatBytes(bytes) {
      const value = Number(bytes || 0);
      if (value < 1024) return `${value} B`;
      if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
      return `${(value / (1024 * 1024)).toFixed(2)} MB`;
    }

    function escapeHtml(text) {
      return String(text || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function readFileAsBase64(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const result = String(reader.result || "");
          const comma = result.indexOf(",");
          if (comma < 0) {
            reject(new Error("Invalid file data"));
            return;
          }
          resolve(result.slice(comma + 1));
        };
        reader.onerror = () => reject(reader.error || new Error("Failed to read file"));
        reader.readAsDataURL(file);
      });
    }

    async function uploadFallbackImage(target, fieldKey, file) {
      if (!file) {
        return;
      }
      if (!String(file.type || "").startsWith("image/")) {
        setStatus("Upload failed: file must be an image.", false);
        return;
      }

      try {
        setStatus(`Uploading ${file.name}...`, true);
        const contentBase64 = await readFileAsBase64(file);
        const res = await apiFetch("/api/upload-fallback-image", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            target,
            filename: file.name,
            content_base64: contentBase64,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          setStatus(data.error || "Upload failed.", false);
          return;
        }

        if (fields[fieldKey]) {
          fields[fieldKey].value = data.path || "";
        }
        setStatus(data.message || "Upload complete.", true);
      } catch (error) {
        setStatus(`Upload failed: ${error}`, false);
      }
    }

    async function setCurrentGeneratedAsFallback(target, fieldKey) {
      try {
        setStatus("Saving current generated image to fallback...", true);
        const res = await apiFetch("/api/fallback/use-current-generated", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target }),
        });
        const data = await res.json();
        if (!res.ok) {
          setStatus(buildApiErrorMessage(data, "Failed to set fallback image from current generated image."), false);
          return;
        }

        if (fields[fieldKey] && data.path) {
          fields[fieldKey].value = data.path;
        }
        setStatus(data.message || "Fallback image updated from current generated image.", true);
      } catch (error) {
        setStatus(`Failed to set fallback image from current generated image: ${error}`, false);
      }
    }

    function setupFallbackUploadButtons() {
      const uploadDefs = [
        {
          buttonId: "uploadFallbackImageBtn",
          inputId: "uploadFallbackImageFile",
          target: "fallback_legacy",
          fieldKey: "fallbackImagePath",
        },
        {
          buttonId: "uploadFallbackDayPortraitBtn",
          inputId: "uploadFallbackDayPortraitFile",
          target: "fallback_day_portrait",
          fieldKey: "fallbackDayPortrait",
        },
        {
          buttonId: "uploadFallbackNightPortraitBtn",
          inputId: "uploadFallbackNightPortraitFile",
          target: "fallback_night_portrait",
          fieldKey: "fallbackNightPortrait",
        },
        {
          buttonId: "uploadFallbackDayLandscapeBtn",
          inputId: "uploadFallbackDayLandscapeFile",
          target: "fallback_day_landscape",
          fieldKey: "fallbackDayLandscape",
        },
        {
          buttonId: "uploadFallbackNightLandscapeBtn",
          inputId: "uploadFallbackNightLandscapeFile",
          target: "fallback_night_landscape",
          fieldKey: "fallbackNightLandscape",
        },
      ];

      for (const def of uploadDefs) {
        const button = document.getElementById(def.buttonId);
        const input = document.getElementById(def.inputId);
        if (!button || !input) {
          continue;
        }
        button.addEventListener("click", () => {
          input.value = "";
          input.click();
        });
        input.addEventListener("change", () => {
          const file = input.files && input.files[0] ? input.files[0] : null;
          uploadFallbackImage(def.target, def.fieldKey, file);
        });
      }
    }

    function setupUseCurrentGeneratedButtons() {
      const defs = [
        {
          buttonId: "setFallbackDayPortraitFromCurrentBtn",
          target: "fallback_day_portrait",
          fieldKey: "fallbackDayPortrait",
        },
        {
          buttonId: "setFallbackNightPortraitFromCurrentBtn",
          target: "fallback_night_portrait",
          fieldKey: "fallbackNightPortrait",
        },
        {
          buttonId: "setFallbackDayLandscapeFromCurrentBtn",
          target: "fallback_day_landscape",
          fieldKey: "fallbackDayLandscape",
        },
        {
          buttonId: "setFallbackNightLandscapeFromCurrentBtn",
          target: "fallback_night_landscape",
          fieldKey: "fallbackNightLandscape",
        },
      ];

      for (const def of defs) {
        const button = document.getElementById(def.buttonId);
        if (!button) {
          continue;
        }
        button.addEventListener("click", () => {
          void setCurrentGeneratedAsFallback(def.target, def.fieldKey);
        });
      }
    }

    function buildDebugAudioUrl(fileName) {
      const params = new URLSearchParams({ name: fileName });
      if (portalToken) {
        params.set("token", portalToken);
      }
      return '/api/debug-audio/file?' + params.toString();
    }

    function decodeDebugAudioName(nameAttr) {
      try {
        return decodeURIComponent(String(nameAttr || ""));
      } catch (error) {
        void error;
        return String(nameAttr || "");
      }
    }

    async function deleteDebugAudioFile(fileName) {
      if (!fileName) {
        return;
      }
      if (!window.confirm(`Delete debug recording \"${fileName}\"?`)) {
        return;
      }

      try {
        const res = await apiFetch('/api/debug-audio/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: fileName })
        });
        const data = await res.json();
        if (!res.ok) {
          debugAudioMetaEl.textContent = buildApiErrorMessage(data, 'Failed to delete debug recording.');
          return;
        }

        const currentSrc = String(debugAudioPlayerEl.src || '');
        if (currentSrc.includes(encodeURIComponent(fileName))) {
          debugAudioPlayerEl.removeAttribute('src');
          debugAudioPlayerEl.load();
        }

        debugAudioMetaEl.textContent = data.message || `Deleted ${fileName}.`;
        await loadDebugAudioList();
      } catch (error) {
        debugAudioMetaEl.textContent = `Failed to delete debug recording: ${error}`;
      }
    }

    async function deleteAllDebugAudioFiles() {
      if (!window.confirm("Delete all debug recordings? This cannot be undone.")) {
        return;
      }

      try {
        const res = await apiFetch('/api/debug-audio/delete-all', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true })
        });
        const data = await res.json();
        if (!res.ok) {
          debugAudioMetaEl.textContent = buildApiErrorMessage(data, 'Failed to delete all debug recordings.');
          return;
        }

        debugAudioPlayerEl.removeAttribute('src');
        debugAudioPlayerEl.load();
        debugAudioMetaEl.textContent = data.message || 'Deleted debug recordings.';
        await loadDebugAudioList();
      } catch (error) {
        debugAudioMetaEl.textContent = `Failed to delete all debug recordings: ${error}`;
      }
    }

    function renderDebugAudioList(data) {
      const rows = data.files || [];
      if (!rows.length) {
        debugAudioListEl.innerHTML = '<div class="event-row info">No debug recordings found.</div>';
        debugAudioPlayerEl.removeAttribute('src');
        debugAudioPlayerEl.load();
        return;
      }

      debugAudioListEl.innerHTML = rows.map((row) => {
        const fileName = escapeHtml(row.name);
        const encodedName = encodeURIComponent(String(row.name || ""));
        const size = escapeHtml(formatBytes(row.size_bytes));
        const modified = escapeHtml(row.modified_at || "");
        return (
          '<div class="debug-audio-row">' +
            '<div>' +
              '<div class="debug-audio-name">' + fileName + '</div>' +
              '<div class="debug-audio-details">' + size + ' • ' + modified + '</div>' +
            '</div>' +
            '<div class="debug-audio-actions">' +
              '<button type="button" class="secondary debug-audio-play-btn" data-name="' + encodedName + '">Play</button>' +
              '<button type="button" class="debug-audio-delete-btn" data-name="' + encodedName + '">Delete</button>' +
            '</div>' +
          '</div>'
        );
      }).join('');
    }

    async function loadDebugAudioList() {
      try {
        const res = await apiFetch('/api/debug-audio?limit=30');
        const data = await res.json();
        if (!res.ok) {
          debugAudioMetaEl.textContent = data.error || 'Failed to load debug recordings.';
          debugAudioListEl.innerHTML = '<div class="event-row failure">Debug recordings unavailable.</div>';
          return;
        }
        debugAudioMetaEl.textContent = 'Showing ' + (data.files ? data.files.length : 0) + ' recordings from: ' + (data.directory || '(unknown)');
        renderDebugAudioList(data);
      } catch (error) {
        debugAudioMetaEl.textContent = `Failed to load debug recordings: ${error}`;
        debugAudioListEl.innerHTML = '<div class="event-row failure">Debug recordings unavailable.</div>';
      }
    }

    function collectOptionValues() {
      return {
        web_enabled: fields.webEnabled.checked,
        web_host: fields.webHost.value.trim(),
        web_port: Number(fields.webPort.value),
        web_admin_token: fields.webAdminToken.value,
        weather_refresh_seconds: Number(fields.weatherRefresh.value),
        weather_timezone: fields.weatherTimezone.value.trim(),
        display_weather_background: fields.weatherBg.value.trim(),
        audio_recording_duration_seconds: Number(fields.audioDuration.value),
        audio_gain_db: Number(fields.audioGainDb.value),
        debug_audio_enabled: fields.debugAudioEnabled.checked,
        debug_audio_path: fields.debugAudioPath.value.trim(),
        display_width: Number(fields.displayWidth.value),
        display_height: Number(fields.displayHeight.value),
        font_path: fields.fontPath.value.trim(),
        font_fallback_path: fields.fontFallbackPath.value.trim(),
        font_size_title: Number(fields.fontSizeTitle.value),
        font_size_subtitle: Number(fields.fontSizeSubtitle.value),
        orientation_strategy: fields.orientationStrategy.value,
        max_square_size: Number(fields.maxSquareSize.value),
        fallback_image_path: fields.fallbackImagePath.value.trim(),
        portrait_album_background_color: fields.portraitAlbumBackgroundColor.value.trim(),
        small_album_cover_px: Number(fields.smallAlbumCoverPx.value),
        text_wrap_break_long_words: boolFromSelect(fields.textWrapBreakLongWords.value),
        text_wrap_hyphenate: boolFromSelect(fields.textWrapHyphenate.value),
        text_line_spacing_px: Number(fields.textLineSpacingPx.value),
        ai_dot_margin_x_px: Number(fields.aiDotMarginXPx.value),
        ai_dot_margin_y_px: Number(fields.aiDotMarginYPx.value),
        backdrop_blur_radius: Number(fields.backdropBlurRadius.value),
        backdrop_darken_alpha: Number(fields.backdropDarkenAlpha.value),
        backdrop_use_gradient: boolFromSelect(fields.backdropUseGradient.value),
        openai_enabled: fields.openaiEnabled.checked,
        music_detection_enabled: fields.musicDetectionEnabled.checked,
        openai_model: fields.openaiModel.value.trim(),
        openai_prompt_style: fields.openaiPromptStyle.value.trim(),
        openai_api_key: fields.openaiApiKey.value,
        ai_provider: fields.aiProvider ? fields.aiProvider.value : "openai",
        pixazo_model: fields.pixazoModel ? fields.pixazoModel.value : "flux-schnell",
        pixazo_api_key: fields.pixazoApiKey ? fields.pixazoApiKey.value : "",
        selected_orientation: fields.selectedOrientation.value,
        text_alignment_portrait: fields.portraitAlign.value,
        text_alignment_landscape: fields.landscapeAlign.value,
        text_offset_left_px_portrait: Number(fields.portraitTextOffsetLeftPx.value),
        text_offset_right_px_portrait: Number(fields.portraitTextOffsetRightPx.value),
        text_offset_top_px_portrait: Number(fields.portraitTextOffsetTopPx.value),
        text_offset_bottom_px_portrait: Number(fields.portraitTextOffsetBottomPx.value),
        text_offset_text_shadow_px_portrait: Number(fields.portraitTextShadowPx.value),
        album_offset_left_px_portrait: Number(fields.portraitAlbumOffsetLeftPx.value),
        album_offset_right_px_portrait: Number(fields.portraitAlbumOffsetRightPx.value),
        album_offset_top_px_portrait: Number(fields.portraitAlbumOffsetTopPx.value),
        album_offset_bottom_px_portrait: Number(fields.portraitAlbumOffsetBottomPx.value),
        fallback_day_portrait: fields.fallbackDayPortrait.value.trim(),
        fallback_night_portrait: fields.fallbackNightPortrait.value.trim(),
        text_offset_left_px_landscape: Number(fields.landscapeTextOffsetLeftPx.value),
        text_offset_right_px_landscape: Number(fields.landscapeTextOffsetRightPx.value),
        text_offset_top_px_landscape: Number(fields.landscapeTextOffsetTopPx.value),
        text_offset_bottom_px_landscape: Number(fields.landscapeTextOffsetBottomPx.value),
        text_offset_text_shadow_px_landscape: Number(fields.landscapeTextShadowPx.value),
        album_offset_left_px_landscape: Number(fields.landscapeAlbumOffsetLeftPx.value),
        album_offset_right_px_landscape: Number(fields.landscapeAlbumOffsetRightPx.value),
        album_offset_top_px_landscape: Number(fields.landscapeAlbumOffsetTopPx.value),
        album_offset_bottom_px_landscape: Number(fields.landscapeAlbumOffsetBottomPx.value),
        fallback_day_landscape: fields.fallbackDayLandscape.value.trim(),
        fallback_night_landscape: fields.fallbackNightLandscape.value.trim(),
        weather_api_key: fields.weatherApiKey.value,
        geo_coordinates: fields.geoCoordinates.value.trim(),
        debounce_seconds: Number(fields.debounceSeconds.value),
        cache_ttl_seconds: Number(fields.cacheTtlSeconds.value),
        cache_size: Number(fields.cacheSize.value),
        log_file_path: fields.logFilePath.value.trim(),
        lighting_day: fields.lightingDay.value,
        lighting_twilight: fields.lightingTwilight.value,
        lighting_night: fields.lightingNight.value
      };
    }

    async function loadOptions() {
      const res = await apiFetch("/api/config-options");
      const data = await res.json();
      if (!res.ok) {
        setStatus(data.error || "Failed to load options.", false);
        return;
      }
      applyOptionValues(data);
      if (data.web_admin_token_configured && !portalToken) {
        ensurePortalToken(true);
      }
      setStatus("Options loaded.", true);
    }

    async function saveOptions() {
      const payload = collectOptionValues();
      const res = await apiFetch("/api/config-options", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus(data.error || "Failed to save options.", false);
        return;
      }
      setStatus(data.message || "Options saved.", true);
      await loadImageState();
      await loadEvents();
      await loadCacheStats();
      startEventStream();
    }

    function renderImage(meta) {
      if (!meta.image_exists) {
        imageBox.innerHTML = '<div class="placeholder">Image not found at:<br>' + (meta.selected_image_path || "(none)") + '</div>';
        return;
      }
      const stamp = Date.now();
      imageBox.innerHTML = '<img alt="current selected image" src="/current-image?t=' + stamp + '" />';
    }

    async function loadImageState() {
      const res = await apiFetch("/api/current-image");
      const data = await res.json();
      if (!res.ok) {
        metaEl.textContent = data.error || "Failed to load image state.";
        imageBox.innerHTML = '<div class="placeholder">No preview available.</div>';
        return;
      }
      metaEl.textContent = JSON.stringify(data, null, 2);
      renderImage(data);
    }

    function renderEvents(data) {
      const rows = data.events || [];
      if (!rows.length) {
        eventLogEl.innerHTML = '<div class="event-row info">No events available yet.</div>';
        return;
      }
      eventLogEl.innerHTML = rows.map((evt) => {
        const cls = evt.kind || "info";
        const ts = evt.timestamp ? '[' + evt.timestamp + '] ' : '';
        const level = evt.level ? '(' + evt.level + ') ' : '';
        const msg = (evt.message || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        return '<div class="event-row ' + cls + '">' + ts + level + msg + '</div>';
      }).join('');
    }

    async function loadEvents() {
      const res = await apiFetch('/api/events?limit=120');
      const data = await res.json();
      if (!res.ok) {
        eventMetaEl.textContent = data.error || 'Failed to load event log.';
        eventLogEl.innerHTML = '<div class="event-row failure">Event log unavailable.</div>';
        return;
      }
      eventMetaEl.textContent = 'Showing ' + (data.events ? data.events.length : 0) + ' latest events from: ' + (data.log_path || '(unknown)');
      renderEvents(data);
    }

    async function clearEvents() {
      if (!window.confirm('Clear the event log now?')) {
        return;
      }

      try {
        const res = await apiFetch('/api/events/clear', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true })
        });
        const data = await res.json();
        if (!res.ok) {
          eventMetaEl.textContent = buildApiErrorMessage(data, 'Failed to clear event log.');
          return;
        }

        eventMetaEl.textContent = data.message || 'Event log cleared.';
        await loadEvents();
      } catch (error) {
        eventMetaEl.textContent = `Failed to clear event log: ${error}`;
      }
    }

    async function restartMainApp() {
      if (!window.confirm('Restart the main now-playing app service now?')) {
        return;
      }

      try {
        restartInFlight = true;
        const restartBtn = document.getElementById("restartAppBtn");
        if (restartBtn) {
          restartBtn.disabled = true;
        }
        setStatus('Restarting main app service...', true);
        const res = await apiFetch('/api/app/restart', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true })
        });
        const data = await res.json();
        if (!res.ok) {
          setStatus(buildApiErrorMessage(data, 'Failed to restart app service.'), false);
          restartInFlight = false;
          if (restartBtn) {
            restartBtn.disabled = false;
          }
          return;
        }

        setStatus(data.message || 'Restart signal sent to main app service.', true);
        await waitForAppActiveAfterRestart();
      } catch (error) {
        setStatus(`Failed to restart app service: ${error}`, false);
      } finally {
        restartInFlight = false;
        const restartBtn = document.getElementById("restartAppBtn");
        if (restartBtn) {
          restartBtn.disabled = false;
        }
      }
    }

    function renderAppServiceStatus(data) {
      if (!appServiceMetaEl) {
        return;
      }

      if (!data || typeof data !== 'object') {
        appServiceMetaEl.textContent = 'App service status unavailable.';
        return;
      }

      const lines = [];
      lines.push(`Service: now-playing.service`);
      lines.push(`Active state: ${data.active_state || 'unknown'}`);
      if (typeof data.ok === 'boolean') {
        lines.push(`Healthy: ${data.ok ? 'yes' : 'no'}`);
      }
      if (typeof data.returncode === 'number') {
        lines.push(`Status command code: ${data.returncode}`);
      }
      if (data.checked_at) {
        lines.push(`Checked at: ${data.checked_at}`);
      }
      if (data.stderr) {
        lines.push(`systemctl stderr: ${data.stderr}`);
      }
      appServiceMetaEl.textContent = lines.join('\\n');
    }

    async function loadAppServiceStatus(silent = false) {
      try {
        const res = await apiFetch('/api/app/status');
        const data = await res.json();
        if (!res.ok) {
          if (!silent) {
            setStatus(buildApiErrorMessage(data, 'Failed to load app service status.'), false);
          }
          renderAppServiceStatus({ active_state: 'unknown', ok: false, stderr: data && data.error ? String(data.error) : '' });
          return null;
        }

        renderAppServiceStatus(data);
        if (!silent && !restartInFlight) {
          setStatus(`App service state: ${data.active_state || 'unknown'}`, !!data.ok);
        }
        return data;
      } catch (error) {
        if (!silent) {
          setStatus(`Failed to load app service status: ${error}`, false);
        }
        renderAppServiceStatus({ active_state: 'unknown', ok: false, stderr: String(error) });
        return null;
      }
    }

    async function waitForAppActiveAfterRestart() {
      const attempts = 60;
      for (let i = 0; i < attempts; i += 1) {
        const data = await loadAppServiceStatus(true);
        if (data && data.ok) {
          setStatus('App restart complete: service is active.', true);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      setStatus('Restart requested, but app did not report active before timeout.', false);
    }

    function formatCacheStats(data) {
      const lines = [];
      lines.push(`SQLite DB: ${data.database_path || "(unknown)"}`);
      lines.push(`DB size: ${Number(data.database_size_bytes || 0).toLocaleString()} bytes`);

      const enrichment = data.enrichment_cache || {};
      lines.push(`Enrichment entries: ${enrichment.entry_count ?? 0}`);
      if (enrichment.newest_updated_at) {
        lines.push(`Enrichment latest: ${enrichment.newest_updated_at}`);
      }
      if (enrichment.oldest_updated_at) {
        lines.push(`Enrichment oldest: ${enrichment.oldest_updated_at}`);
      }

      const weather = data.weather_cache || {};
      lines.push(`Weather cache present: ${weather.present ? "yes" : "no"}`);
      if (weather.updated_at) {
        lines.push(`Weather updated at: ${weather.updated_at}`);
      }
      if (weather.fetched_at) {
        lines.push(`Weather fetched at: ${weather.fetched_at}`);
      }

      return lines.join("\\n");
    }

    async function loadCacheStats() {
      try {
        const res = await apiFetch('/api/cache-stats');
        const data = await res.json();
        if (!res.ok) {
          cacheStatsEl.textContent = data.error || 'Failed to load cache stats.';
          return;
        }
        cacheStatsEl.textContent = formatCacheStats(data);
      } catch (error) {
        cacheStatsEl.textContent = `Failed to load cache stats: ${error}`;
      }
    }

    function applyStreamSnapshot(snapshot) {
      if (snapshot.current_image) {
        metaEl.textContent = JSON.stringify(snapshot.current_image, null, 2);
        renderImage(snapshot.current_image);
      }

      if (snapshot.events) {
        eventMetaEl.textContent = 'Showing ' + (snapshot.events.events ? snapshot.events.events.length : 0) + ' latest events from: ' + (snapshot.events.log_path || '(unknown)');
        renderEvents(snapshot.events);
      }

      if (snapshot.cache_stats) {
        cacheStatsEl.textContent = formatCacheStats(snapshot.cache_stats);
      }

    }

    const validationTimers = {};

    async function validateApiKey(fieldId, indicatorId, validationType, useStoredWhenMasked = false) {
      const field = document.getElementById(fieldId);
      const indicator = document.getElementById(indicatorId);
      if (!field || !indicator) return;

      const value = field.value.trim();
      if (!value || (value === MASKED_SECRET_VALUE && !useStoredWhenMasked)) {
        indicator.textContent = '';
        indicator.className = 'validation-indicator';
        return;
      }

      indicator.textContent = '⟳';
      indicator.className = 'validation-indicator pending';

      try {
        const res = await apiFetch('/api/validate-key', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            key_type: validationType,
            key_value: value,
            use_stored_when_masked: !!useStoredWhenMasked
          })
        });
        const data = await res.json();
        if (data.valid) {
          indicator.textContent = '✓';
          indicator.className = 'validation-indicator valid';
        } else {
          indicator.textContent = '✗';
          indicator.className = 'validation-indicator invalid';
        }
      } catch (error) {
        indicator.textContent = '✗';
        indicator.className = 'validation-indicator invalid';
      }
    }

    function setupValidationListeners() {
      const validationFields = [
        { fieldId: 'webAdminToken', indicatorId: 'webAdminTokenValidation', type: 'admin_token' },
        { fieldId: 'openaiApiKey', indicatorId: 'openaiApiKeyValidation', type: 'openai_key' },
        { fieldId: 'weatherApiKey', indicatorId: 'weatherApiKeyValidation', type: 'openweather_key' },
        { fieldId: 'pixazoApiKey', indicatorId: 'pixazoApiKeyValidation', type: 'pixazo_key' }
      ];

      for (const config of validationFields) {
        const field = document.getElementById(config.fieldId);
        if (field) {
          field.addEventListener('blur', () => {
            if (validationTimers[config.fieldId]) {
              clearTimeout(validationTimers[config.fieldId]);
            }
            validationTimers[config.fieldId] = setTimeout(() => {
              validateApiKey(config.fieldId, config.indicatorId, config.type);
            }, 300);
          });
        }
      }
    }

    function startEventStream() {
      if (eventSource) {
        eventSource.close();
      }

      const tokenQuery = portalToken ? ('?token=' + encodeURIComponent(portalToken)) : '';
      eventSource = new EventSource('/api/stream' + tokenQuery);

      eventSource.onmessage = (evt) => {
        try {
          const payload = JSON.parse(evt.data);
          applyStreamSnapshot(payload);
        } catch (error) {
          void error;
        }
      };

      eventSource.onerror = () => {
        if (eventSource) {
          eventSource.close();
        }
        setTimeout(startEventStream, 4000);
      };
    }

    fields.openaiEnabled.addEventListener("change", applyVisibilityRules);
    fields.debugAudioEnabled.addEventListener("change", applyVisibilityRules);
    fields.selectedOrientation.addEventListener("change", applyVisibilityRules);
    document.getElementById("saveOptionsBtn").addEventListener("click", saveOptions);
    document.getElementById("reloadBtn").addEventListener("click", async () => {
      await loadOptions();
      await loadImageState();
      await loadEvents();
      await loadAppServiceStatus(true);
    });
    document.getElementById("restartAppBtn").addEventListener("click", restartMainApp);
    document.getElementById("refreshAppStatusBtn").addEventListener("click", () => loadAppServiceStatus(false));
    document.getElementById("refreshImageBtn").addEventListener("click", loadImageState);
    document.getElementById("refreshEventsBtn").addEventListener("click", loadEvents);
    document.getElementById("clearEventsBtn").addEventListener("click", clearEvents);
    document.getElementById("refreshDebugAudioBtn").addEventListener("click", loadDebugAudioList);
    document.getElementById("deleteAllDebugAudioBtn").addEventListener("click", deleteAllDebugAudioFiles);
    document.getElementById("testOpenaiKeyBtn").addEventListener("click", () => {
      validateApiKey('openaiApiKey', 'openaiApiKeyValidation', 'openai_key', true);
    });
    document.getElementById("testWeatherKeyBtn").addEventListener("click", () => {
      validateApiKey('weatherApiKey', 'weatherApiKeyValidation', 'openweather_key', true);
    });
    document.getElementById("testPixazoKeyBtn").addEventListener("click", () => {
      validateApiKey('pixazoApiKey', 'pixazoApiKeyValidation', 'pixazo_key', true);
    });
    document.getElementById("refreshCacheStatsBtn").addEventListener("click", loadCacheStats);
    setupFallbackUploadButtons();
    setupUseCurrentGeneratedButtons();

    if (fields.aiProvider) {
      fields.aiProvider.addEventListener("change", applyVisibilityRules);
    }

    document.getElementById("previewPromptBtn").addEventListener("click", async () => {
      const el = document.getElementById("promptPreview");
      if (!el) return;
      el.value = "Loading...";
      try {
        const res = await apiFetch("/api/preview-prompt", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(collectOptionValues())
        });
        const data = await res.json();
        el.value = res.ok ? (data.prompt || "") : (data.error || "Failed to load preview.");
      } catch (error) {
        el.value = `Error: ${error}`;
      }
    });

    document.getElementById("testAiImageBtn").addEventListener("click", async () => {
      renderAiTestImage("", "Generating test image...");
      try {
        const res = await apiFetch("/api/test-ai-image", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(collectOptionValues())
        });
        const data = await res.json();
        if (!res.ok) {
          renderAiTestImage("", buildApiErrorMessage(data, "Failed to generate test image."));
          return;
        }
        const stamp = Date.now();
        renderAiTestImage('/api/test-ai-image/file?t=' + stamp, data.message || "Test image generated.");
        const previewEl = document.getElementById("promptPreview");
        if (previewEl && data.prompt) {
          previewEl.value = data.prompt;
        }
      } catch (error) {
        renderAiTestImage("", `Failed to generate test image: ${error}`);
      }
    });

    debugAudioListEl.addEventListener("click", (evt) => {
      const target = evt.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      const fileName = decodeDebugAudioName(target.getAttribute("data-name") || "");
      if (!fileName) {
        return;
      }
      if (target.classList.contains("debug-audio-play-btn")) {
        debugAudioPlayerEl.src = buildDebugAudioUrl(fileName);
        debugAudioPlayerEl.play().catch(() => {});
        return;
      }
      if (target.classList.contains("debug-audio-delete-btn")) {
        void deleteDebugAudioFile(fileName);
      }
    });

    (async () => {
      setupSectionTabs();
      applyHoverHints();
      setupValidationListeners();
      await loadOptions();
      await loadImageState();
      await loadEvents();
      await loadDebugAudioList();
      await loadAppServiceStatus(true);
      await loadCacheStats();
      startEventStream();
    })();
  </script>
</body>
</html>
"""


def load_config_data() -> Dict[str, Any]:
  return SETTINGS_STORE.load_config()


def backup_config() -> Path | None:
  return SETTINGS_STORE.backup_database()


def _build_preview_prompt_fallback(config: Dict[str, Any]) -> str:
  openai_cfg = config.get("openai", {}) if isinstance(config.get("openai", {}), dict) else {}
  weather_cfg = config.get("weather", {}) if isinstance(config.get("weather", {}), dict) else {}
  lighting_cfg = config.get("lighting", {}) if isinstance(config.get("lighting", {}), dict) else {}

  style_txt = str(openai_cfg.get("prompt_style", "") or "").strip() or "80s anime style"
  geo = str(weather_cfg.get("geo_coordinates", "") or "").strip()

  hour = datetime.now().hour
  if 17 <= hour <= 19 or 6 <= hour <= 8:
    lighting_txt = str(lighting_cfg.get("twilight", "") or "").strip()
    period = "twilight"
  elif 7 <= hour <= 19:
    lighting_txt = str(lighting_cfg.get("day", "") or "").strip()
    period = "day"
  else:
    lighting_txt = str(lighting_cfg.get("night", "") or "").strip()
    period = "night"

  city = "configured location"
  if geo:
    try:
      lat_str, lon_str = geo.split(",", 1)
      city = f"location at {lat_str.strip()}, {lon_str.strip()}"
    except Exception:
      pass

  return (
    f"Generate an image in an {style_txt} style of location accurate {city} architecture with no signage. "
    f"Set the scene at the current time of day ({period}). "
    f"{lighting_txt} "
    f"Incorporate the area's local train system and accurate city skyline into the composition "
    f"and ensure that the major details are cropped within a centered 480px wide area."
  ).strip()


def _build_preview_prompt(config: Dict[str, Any]) -> str:
  # Keep prompt preview behavior aligned with runtime AI prompt generation logic.
  try:
    ai_service = AIBackgroundService(config_override=config, output_override=str(TEST_AI_PREVIEW_PATH))
    ai_service._prepare_context()
    return ai_service._build_dynamic_prompt()
  except Exception:
    return _build_preview_prompt_fallback(config)


def build_preview_config_from_options(options: Dict[str, Any]) -> Dict[str, Any]:
  config = load_config_data()
  return apply_config_options(config, options, persist_toggle_updates=False)


def load_toggle_state() -> Dict[str, Any]:
  return SETTINGS_STORE.load_toggle_state()


def is_daytime() -> bool:
    hour = datetime.now().hour
    return 7 <= hour <= 19


def resolve_current_image(config: Dict[str, Any], toggle_state: Dict[str, Any]) -> Path:
    display_cfg = config.get("display", {}) if isinstance(config.get("display", {}), dict) else {}
    image_cfg = config.get("image", {}) if isinstance(config.get("image", {}), dict) else {}

    orientation = str(toggle_state.get("orientation", "portrait")).lower()
    if orientation not in ("portrait", "landscape"):
        orientation = "portrait"

    fallback_mode = bool(toggle_state.get("ai_bg_fallback_mode", False))

    if fallback_mode:
        suffix = "day" if is_daytime() else "night"
        key = f"fallback_image_path_{suffix}_{orientation}"
        selected = image_cfg.get(key) or image_cfg.get("fallback_image_path") or display_cfg.get("weather_background_image")
    else:
        selected = display_cfg.get("weather_background_image")

    if not selected:
        return PROJECT_ROOT / "resources" / "ai_screensaver.png"

    p = Path(str(selected))
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


def resolve_rendered_preview_path(config: Dict[str, Any]) -> Path:
    web_cfg = config.get("web_interface", {}) if isinstance(config.get("web_interface", {}), dict) else {}
    configured = web_cfg.get("preview_image_path")
    if configured:
        p = Path(str(configured))
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()
    return (PROJECT_ROOT / "config" / "current_display_preview.png").resolve()


def resolve_preview_rotation_degrees(config: Dict[str, Any], toggle_state: Dict[str, Any], is_rendered_preview: bool) -> int:
    display_cfg = config.get("display", {}) if isinstance(config.get("display", {}), dict) else {}
    orientation = str(toggle_state.get("orientation", "portrait")).lower()

    # Prefer runtime toggle-state rotation, because this reflects the active orientation cycle
    # used by the running app (button C / toggle-state updates).
    rotation_raw = toggle_state.get("rotation")
    orientation_rotation_raw: Any = None

    if isinstance(rotation_raw, dict):
        orientation_rotation_raw = rotation_raw.get(orientation)
    else:
        orientation_rotation_raw = rotation_raw

    active_rotation_degrees: Optional[int] = None

    if isinstance(orientation_rotation_raw, bool):
        if orientation == "portrait":
            active_rotation_degrees = 270 if orientation_rotation_raw else 90
        else:
            active_rotation_degrees = 180 if orientation_rotation_raw else 0

    if active_rotation_degrees is None and orientation_rotation_raw is not None:
        try:
            active_rotation_degrees = int(orientation_rotation_raw) % 360
        except (TypeError, ValueError):
            pass

    if active_rotation_degrees is None:
        if orientation == "portrait":
            raw = display_cfg.get("portrait_rotate_degrees", 90)
        else:
            raw = display_cfg.get("landscape_rotate_degrees", 0)

        try:
            active_rotation_degrees = int(raw) % 360
        except (TypeError, ValueError):
            active_rotation_degrees = 0

    # Rendered previews are already transformed for hardware orientation.
    # Counter-rotate in the admin portal so browser preview matches human view.
    if is_rendered_preview:
        return (-active_rotation_degrees) % 360

    return active_rotation_degrees


def build_current_image_payload(config: Dict[str, Any], toggle: Dict[str, Any]) -> Dict[str, Any]:
    preview_path = resolve_rendered_preview_path(config)
    is_rendered_preview = preview_path.exists()
    image_path = preview_path if is_rendered_preview else resolve_current_image(config, toggle)
    rotate_degrees = resolve_preview_rotation_degrees(config, toggle, is_rendered_preview)
    openai_enabled = bool(toggle.get("openai_enabled", True))
    manual_fallback_mode = bool(toggle.get("ai_bg_fallback_mode", False))
    effective_fallback_mode = (not openai_enabled) or manual_fallback_mode
    return {
      "selected_image_path": str(image_path),
      "image_exists": image_path.exists(),
      "orientation": str(toggle.get("orientation", "portrait")).lower(),
      "ai_bg_fallback_mode": effective_fallback_mode,
      "daytime_assumption": is_daytime(),
      "is_rendered_preview": is_rendered_preview,
      "preview_rotation_degrees": rotate_degrees,
    }


def resolve_web_admin_token(config: Dict[str, Any]) -> str:
    web_cfg = config.get("web_interface", {}) if isinstance(config.get("web_interface", {}), dict) else {}
    return str(web_cfg.get("admin_token", "")).strip()


_SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key\s*[:=]\s*)([^\s,;]+)", flags=re.IGNORECASE),
    re.compile(r"(client[_-]?secret\s*[:=]\s*)([^\s,;]+)", flags=re.IGNORECASE),
    re.compile(r"(authorization\s*[:=]\s*bearer\s+)([^\s,;]+)", flags=re.IGNORECASE),
    re.compile(r"\b(sk-[^\s'\",}]{6,})\b"),
]


def redact_sensitive_text(message: str) -> str:
    redacted = message
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(lambda m: f"{m.group(1)}***REDACTED***", redacted)
        else:
            redacted = pattern.sub("***REDACTED***", redacted)
    return redacted


def resolve_log_file_path(config: Dict[str, Any]) -> Path:
    log_cfg = config.get("log", {}) if isinstance(config.get("log", {}), dict) else {}
    configured = log_cfg.get("log_file_path")
    if configured:
        p = Path(str(configured))
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()
    return (PROJECT_ROOT / "log" / "now_playing.log").resolve()


def resolve_debug_audio_path(config: Dict[str, Any]) -> Path:
    audio_cfg = config.get("audio", {}) if isinstance(config.get("audio", {}), dict) else {}
    configured = str(audio_cfg.get("debugaudio_path", "")).strip()
    if configured:
        p = Path(configured)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()
    return (PROJECT_ROOT / "debug_audio").resolve()


def list_debug_audio_entries(config: Dict[str, Any], limit: int) -> Dict[str, Any]:
    debug_dir = resolve_debug_audio_path(config)
    max_items = max(1, min(limit, 200))
    allowed_suffixes = {".wav", ".mp3", ".ogg", ".m4a", ".flac"}

    if not debug_dir.exists() or not debug_dir.is_dir():
        return {"directory": str(debug_dir), "files": []}

    files: list[Path] = []
    for entry in debug_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in allowed_suffixes:
            continue
        files.append(entry)

    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    payload: list[Dict[str, Any]] = []
    for item in files[:max_items]:
        stat = item.stat()
        payload.append(
            {
                "name": item.name,
                "size_bytes": int(stat.st_size),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
        )

    return {"directory": str(debug_dir), "files": payload}


def resolve_debug_audio_file(config: Dict[str, Any], name: str) -> Path:
    debug_dir = resolve_debug_audio_path(config).resolve()
    requested = Path(str(name or "")).name
    if not requested or requested in (".", ".."):
        raise ValueError("Missing debug audio file name.")

    candidate = (debug_dir / requested).resolve()
    try:
        candidate.relative_to(debug_dir)
    except ValueError as exc:
        raise PermissionError("Invalid debug audio file path.") from exc

    if candidate.suffix.lower() not in {".wav", ".mp3", ".ogg", ".m4a", ".flac"}:
        raise ValueError("Unsupported debug audio file type.")

    return candidate


def delete_debug_audio_file(config: Dict[str, Any], name: str) -> Path:
    target = resolve_debug_audio_file(config, name)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("Debug audio file not found.")
    target.unlink()
    return target


def delete_all_debug_audio_files(config: Dict[str, Any]) -> Dict[str, Any]:
  listing = list_debug_audio_entries(config, limit=2000)
  rows = listing.get("files") if isinstance(listing, dict) else []
  if not isinstance(rows, list):
    rows = []

  deleted = 0
  failed: list[str] = []
  for row in rows:
    name = str((row or {}).get("name", "")).strip() if isinstance(row, dict) else ""
    if not name:
      continue
    try:
      delete_debug_audio_file(config, name)
      deleted += 1
    except Exception:
      failed.append(name)

  return {
    "deleted_count": deleted,
    "failed_count": len(failed),
    "failed_names": failed,
  }


def classify_event_kind(level: str, message: str) -> str:
    text = f"{(level or '').lower()} {message.lower()}"
    if any(token in text for token in ["fallback", "fallback mode", "used fallback"]):
        return "fallback"
    if any(token in text for token in ["error", "exception", "failed", "traceback", "could not"]):
        return "failure"
    return "info"


def parse_event_line(raw_line: str) -> Dict[str, str]:
    line = raw_line.rstrip("\r\n")
    parts = line.split(" :: ", 2)
    timestamp = ""
    level = "INFO"
    message = line

    if len(parts) == 3:
        timestamp, level, message = parts[0], parts[1], parts[2]

    message = redact_sensitive_text(message)
    kind = classify_event_kind(level, message)
    return {
        "timestamp": timestamp,
        "level": level,
        "kind": kind,
        "message": message,
    }


def read_recent_events(log_path: Path, limit: int) -> list[Dict[str, str]]:
    if not log_path.exists() or not log_path.is_file():
        return []

    max_items = max(1, min(limit, 500))
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        rows = deque(handle, maxlen=max_items)
    return [parse_event_line(row) for row in rows if row.strip()]


def clear_event_log(config: Dict[str, Any]) -> Dict[str, Any]:
    log_path = resolve_log_file_path(config)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cleared_bytes = 0
    if log_path.exists() and log_path.is_file():
        try:
            cleared_bytes = int(log_path.stat().st_size)
        except Exception:
            cleared_bytes = 0

    with log_path.open("w", encoding="utf-8"):
        pass

    return {"log_path": str(log_path), "cleared_bytes": cleared_bytes}


def get_main_service_status() -> Dict[str, Any]:
    command = ["sudo", "-n", "systemctl", "is-active", "now-playing.service"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    checked_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    return {
      "active_state": stdout or "unknown",
      "ok": result.returncode == 0 and stdout == "active",
      "returncode": result.returncode,
      "stderr": stderr,
      "checked_at": checked_at,
    }


def restart_main_service() -> Dict[str, Any]:
    requested_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    command = ["sudo", "-n", "systemctl", "restart", "now-playing.service"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        raise RuntimeError(stderr or "systemctl restart failed")

    status = get_main_service_status()
    return {
        "message": "Restart command sent to now-playing.service.",
        "requested_at": requested_at,
        "active_state": status.get("active_state", "unknown"),
        "status_ok": bool(status.get("ok", False)),
        "checked_at": status.get("checked_at"),
    }


def build_config_options(config: Dict[str, Any]) -> Dict[str, Any]:
    display_cfg = config.get("display", {}) if isinstance(config.get("display", {}), dict) else {}
    weather_cfg = config.get("weather", {}) if isinstance(config.get("weather", {}), dict) else {}
    image_cfg = config.get("image", {}) if isinstance(config.get("image", {}), dict) else {}
    web_cfg = config.get("web_interface", {}) if isinstance(config.get("web_interface", {}), dict) else {}
    openai_cfg = config.get("openai", {}) if isinstance(config.get("openai", {}), dict) else {}
    audio_cfg = config.get("audio", {}) if isinstance(config.get("audio", {}), dict) else {}
    orchestrator_cfg = config.get("orchestrator", {}) if isinstance(config.get("orchestrator", {}), dict) else {}
    log_cfg = config.get("log", {}) if isinstance(config.get("log", {}), dict) else {}
    lighting_cfg = config.get("lighting", {}) if isinstance(config.get("lighting", {}), dict) else {}
    toggle_state = load_toggle_state()

    openai_api_key = str(openai_cfg.get("api_key", ""))
    openai_enabled = bool(toggle_state.get("openai_enabled", bool(openai_api_key.strip())))
    music_detection_enabled = bool(toggle_state.get("music_detection_enabled", True))

    selected_orientation = str(toggle_state.get("orientation", "portrait")).lower()
    if selected_orientation not in ("portrait", "landscape"):
        selected_orientation = "portrait"

    return {
        "web_enabled": bool(web_cfg.get("enabled", True)),
        "web_host": str(web_cfg.get("host", "0.0.0.0")),
        "web_port": int(web_cfg.get("port", 8088)),
      "web_admin_token_configured": bool(resolve_web_admin_token(config)),
        "openai_api_key_configured": bool(openai_api_key.strip()),
        "display_width": int(display_cfg.get("width", 800)),
        "display_height": int(display_cfg.get("height", 480)),
        "font_path": str(display_cfg.get("font_path", "")),
        "font_fallback_path": str(display_cfg.get("font_fallback_path", "")),
        "font_size_title": int(display_cfg.get("font_size_title", 45)),
        "font_size_subtitle": int(display_cfg.get("font_size_subtitle", 30)),
        "text_offset_left_px_portrait": int(display_cfg.get("text_offset_left_px_portrait", 5)),
        "text_offset_right_px_portrait": int(display_cfg.get("text_offset_right_px_portrait", 20)),
        "text_offset_top_px_portrait": int(display_cfg.get("text_offset_top_px_portrait", 0)),
        "text_offset_bottom_px_portrait": int(display_cfg.get("text_offset_bottom_px_portrait", 80)),
        "text_offset_text_shadow_px_portrait": int(display_cfg.get("text_offset_text_shadow_px_portrait", 4)),
        "text_offset_left_px_landscape": int(display_cfg.get("text_offset_left_px_landscape", 0)),
        "text_offset_right_px_landscape": int(display_cfg.get("text_offset_right_px_landscape", 0)),
        "text_offset_top_px_landscape": int(display_cfg.get("text_offset_top_px_landscape", 0)),
        "text_offset_bottom_px_landscape": int(display_cfg.get("text_offset_bottom_px_landscape", 0)),
        "text_offset_text_shadow_px_landscape": int(display_cfg.get("text_offset_text_shadow_px_landscape", 4)),
        "album_offset_left_px_portrait": int(display_cfg.get("album_offset_left_px_portrait", 0)),
        "album_offset_right_px_portrait": int(display_cfg.get("album_offset_right_px_portrait", 14)),
        "album_offset_top_px_portrait": int(display_cfg.get("album_offset_top_px_portrait", 49)),
        "album_offset_bottom_px_portrait": int(display_cfg.get("album_offset_bottom_px_portrait", 0)),
        "album_offset_left_px_landscape": int(display_cfg.get("album_offset_left_px_landscape", 0)),
        "album_offset_right_px_landscape": int(display_cfg.get("album_offset_right_px_landscape", 0)),
        "album_offset_top_px_landscape": int(display_cfg.get("album_offset_top_px_landscape", 0)),
        "album_offset_bottom_px_landscape": int(display_cfg.get("album_offset_bottom_px_landscape", 0)),
        "backdrop_blur_radius": int(display_cfg.get("backdrop_blur_radius", 12)),
        "backdrop_darken_alpha": int(display_cfg.get("backdrop_darken_alpha", 120)),
        "backdrop_use_gradient": bool(display_cfg.get("backdrop_use_gradient", False)),
        "small_album_cover_px": int(display_cfg.get("small_album_cover_px", 450)),
        "orientation_strategy": str(image_cfg.get("orientation_strategy", "cover")),
        "max_square_size": int(image_cfg.get("max_square_size", 1024)),
        "fallback_image_path": str(image_cfg.get("fallback_image_path", "")),
        "weather_refresh_seconds": int(weather_cfg.get("background_refresh_seconds", 3600)),
        "weather_timezone": str(weather_cfg.get("timezone", "Australia/Melbourne")),
        "weather_api_key": "",
        "weather_api_key_configured": bool(str(weather_cfg.get("openweathermap_api_key", "")).strip()),
        "geo_coordinates": str(weather_cfg.get("geo_coordinates", "")),
        "display_weather_background": str(display_cfg.get("weather_background_image", "")),
        "audio_recording_duration_seconds": int(audio_cfg.get("recording_duration_seconds", 5)),
        "audio_gain_db": float(audio_cfg.get("gain_db", 0.0)),
        "debug_audio_enabled": bool(audio_cfg.get("debugaudio", False)),
        "debug_audio_path": str(audio_cfg.get("debugaudio_path", "")),
        "openai_enabled": openai_enabled,
        "music_detection_enabled": music_detection_enabled,
        "openai_model": str(openai_cfg.get("model", "")),
        "openai_prompt_style": str(openai_cfg.get("prompt_style", "")),
        "pixazo_api_key": "",
        "pixazo_api_key_configured": bool(str(openai_cfg.get("pixazo_api_key", "")).strip()),
        "ai_provider": str(openai_cfg.get("provider", "openai")),
        "pixazo_model": str(openai_cfg.get("pixazo_model", "flux-schnell")),
        "ai_dot_margin_x_px": int(display_cfg.get("ai_dot_margin_x_px", 55)),
        "ai_dot_margin_y_px": int(display_cfg.get("ai_dot_margin_y_px", 45)),
        "lighting_day": str(lighting_cfg.get("day", "")),
        "lighting_twilight": str(lighting_cfg.get("twilight", "")),
        "lighting_night": str(lighting_cfg.get("night", "")),
        "selected_orientation": selected_orientation,
        "text_alignment_portrait": str(display_cfg.get("text_alignment_portrait", "left")),
        "text_alignment_landscape": str(display_cfg.get("text_alignment_landscape", "left")),
        "text_wrap_break_long_words": bool(display_cfg.get("text_wrap_break_long_words", True)),
        "text_wrap_hyphenate": bool(display_cfg.get("text_wrap_hyphenate", False)),
        "text_line_spacing_px": int(display_cfg.get("text_line_spacing_px", 4)),
        "portrait_album_background_color": str(display_cfg.get("portrait_album_background_color", "black")),
        "fallback_day_portrait": str(image_cfg.get("fallback_image_path_day_portrait", "")),
        "fallback_night_portrait": str(image_cfg.get("fallback_image_path_night_portrait", "")),
        "fallback_day_landscape": str(image_cfg.get("fallback_image_path_day_landscape", "")),
        "fallback_night_landscape": str(image_cfg.get("fallback_image_path_night_landscape", "")),
        "debounce_seconds": int(orchestrator_cfg.get("debounce_seconds", 30)),
        "cache_ttl_seconds": int(orchestrator_cfg.get("cache_ttl_seconds", 86400)),
        "cache_size": int(orchestrator_cfg.get("cache_size", 512)),
        "log_file_path": str(log_cfg.get("log_file_path", "")),
    }


def _set_nested(config: Dict[str, Any], path: list[str], value: Any) -> None:
    current = config
    for key in path[:-1]:
        next_value = current.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            current[key] = next_value
        current = next_value
    current[path[-1]] = value


def apply_config_options(config: Dict[str, Any], options: Dict[str, Any], persist_toggle_updates: bool = True) -> Dict[str, Any]:
  updated = json.loads(json.dumps(config))

  _set_nested(updated, ["web_interface", "enabled"], bool(options.get("web_enabled", True)))
  _set_nested(updated, ["web_interface", "host"], str(options.get("web_host", "0.0.0.0")))
  _set_nested(updated, ["web_interface", "port"], int(options.get("web_port", 8088)))
  web_admin_token = options.get("web_admin_token")
  if isinstance(web_admin_token, str):
    web_admin_token = web_admin_token.strip()
    if web_admin_token and web_admin_token != MASKED_SECRET_VALUE:
      _set_nested(updated, ["web_interface", "admin_token"], web_admin_token)
  _set_nested(updated, ["display", "width"], int(options.get("display_width", 800)))
  _set_nested(updated, ["display", "height"], int(options.get("display_height", 480)))
  _set_nested(updated, ["display", "font_path"], str(options.get("font_path", "")))
  _set_nested(updated, ["display", "font_fallback_path"], str(options.get("font_fallback_path", "")))
  _set_nested(updated, ["display", "font_size_title"], int(options.get("font_size_title", 45)))
  _set_nested(updated, ["display", "font_size_subtitle"], int(options.get("font_size_subtitle", 30)))
  _set_nested(updated, ["display", "text_offset_left_px_portrait"], int(options.get("text_offset_left_px_portrait", 5)))
  _set_nested(updated, ["display", "text_offset_right_px_portrait"], int(options.get("text_offset_right_px_portrait", 20)))
  _set_nested(updated, ["display", "text_offset_top_px_portrait"], int(options.get("text_offset_top_px_portrait", 0)))
  _set_nested(updated, ["display", "text_offset_bottom_px_portrait"], int(options.get("text_offset_bottom_px_portrait", 80)))
  _set_nested(updated, ["display", "text_offset_text_shadow_px_portrait"], int(options.get("text_offset_text_shadow_px_portrait", 4)))
  _set_nested(updated, ["display", "text_offset_left_px_landscape"], int(options.get("text_offset_left_px_landscape", 0)))
  _set_nested(updated, ["display", "text_offset_right_px_landscape"], int(options.get("text_offset_right_px_landscape", 0)))
  _set_nested(updated, ["display", "text_offset_top_px_landscape"], int(options.get("text_offset_top_px_landscape", 0)))
  _set_nested(updated, ["display", "text_offset_bottom_px_landscape"], int(options.get("text_offset_bottom_px_landscape", 0)))
  _set_nested(updated, ["display", "text_offset_text_shadow_px_landscape"], int(options.get("text_offset_text_shadow_px_landscape", 4)))
  _set_nested(updated, ["display", "album_offset_left_px_portrait"], int(options.get("album_offset_left_px_portrait", 0)))
  _set_nested(updated, ["display", "album_offset_right_px_portrait"], int(options.get("album_offset_right_px_portrait", 14)))
  _set_nested(updated, ["display", "album_offset_top_px_portrait"], int(options.get("album_offset_top_px_portrait", 49)))
  _set_nested(updated, ["display", "album_offset_bottom_px_portrait"], int(options.get("album_offset_bottom_px_portrait", 0)))
  _set_nested(updated, ["display", "album_offset_left_px_landscape"], int(options.get("album_offset_left_px_landscape", 0)))
  _set_nested(updated, ["display", "album_offset_right_px_landscape"], int(options.get("album_offset_right_px_landscape", 0)))
  _set_nested(updated, ["display", "album_offset_top_px_landscape"], int(options.get("album_offset_top_px_landscape", 0)))
  _set_nested(updated, ["display", "album_offset_bottom_px_landscape"], int(options.get("album_offset_bottom_px_landscape", 0)))
  _set_nested(updated, ["display", "backdrop_blur_radius"], int(options.get("backdrop_blur_radius", 12)))
  _set_nested(updated, ["display", "backdrop_darken_alpha"], int(options.get("backdrop_darken_alpha", 120)))
  _set_nested(updated, ["display", "backdrop_use_gradient"], bool(options.get("backdrop_use_gradient", False)))
  _set_nested(updated, ["display", "small_album_cover_px"], int(options.get("small_album_cover_px", 450)))
  _set_nested(updated, ["display", "text_alignment_portrait"], str(options.get("text_alignment_portrait", "left")))
  _set_nested(updated, ["display", "text_alignment_landscape"], str(options.get("text_alignment_landscape", "left")))
  _set_nested(updated, ["display", "text_wrap_break_long_words"], bool(options.get("text_wrap_break_long_words", True)))
  _set_nested(updated, ["display", "text_wrap_hyphenate"], bool(options.get("text_wrap_hyphenate", False)))
  _set_nested(updated, ["display", "text_line_spacing_px"], int(options.get("text_line_spacing_px", 4)))
  _set_nested(updated, ["display", "ai_dot_margin_x_px"], int(options.get("ai_dot_margin_x_px", 55)))
  _set_nested(updated, ["display", "ai_dot_margin_y_px"], int(options.get("ai_dot_margin_y_px", 45)))
  _set_nested(updated, ["display", "portrait_album_background_color"], str(options.get("portrait_album_background_color", "black")))
  _set_nested(updated, ["weather", "background_refresh_seconds"], int(options.get("weather_refresh_seconds", 3600)))
  _set_nested(updated, ["weather", "timezone"], str(options.get("weather_timezone", "Australia/Melbourne")))
  _set_nested(updated, ["display", "weather_background_image"], str(options.get("display_weather_background", "")))
  weather_api_key = options.get("weather_api_key")
  if isinstance(weather_api_key, str):
    weather_api_key = weather_api_key.strip()
    if weather_api_key and weather_api_key != MASKED_SECRET_VALUE:
      _set_nested(updated, ["weather", "openweathermap_api_key"], weather_api_key)
  _set_nested(updated, ["weather", "geo_coordinates"], str(options.get("geo_coordinates", "")))

  _set_nested(updated, ["audio", "recording_duration_seconds"], int(options.get("audio_recording_duration_seconds", 5)))
  _set_nested(updated, ["audio", "gain_db"], float(options.get("audio_gain_db", 0.0)))
  debug_audio_enabled = bool(options.get("debug_audio_enabled", False))
  _set_nested(updated, ["audio", "debugaudio"], debug_audio_enabled)
  if debug_audio_enabled:
    _set_nested(updated, ["audio", "debugaudio_path"], str(options.get("debug_audio_path", "")))

  _set_nested(updated, ["image", "fallback_image_path_day_portrait"], str(options.get("fallback_day_portrait", "")))
  _set_nested(updated, ["image", "fallback_image_path_night_portrait"], str(options.get("fallback_night_portrait", "")))
  _set_nested(updated, ["image", "fallback_image_path_day_landscape"], str(options.get("fallback_day_landscape", "")))
  _set_nested(updated, ["image", "fallback_image_path_night_landscape"], str(options.get("fallback_night_landscape", "")))
  _set_nested(updated, ["image", "orientation_strategy"], str(options.get("orientation_strategy", "cover")))
  _set_nested(updated, ["image", "max_square_size"], int(options.get("max_square_size", 1024)))
  fallback_image_path = options.get("fallback_image_path")
  if isinstance(fallback_image_path, str):
    _set_nested(updated, ["image", "fallback_image_path"], fallback_image_path.strip())

  _set_nested(updated, ["openai", "model"], str(options.get("openai_model", "")))
  _set_nested(updated, ["openai", "prompt_style"], str(options.get("openai_prompt_style", "")))
  _set_nested(updated, ["openai", "provider"], str(options.get("ai_provider", "openai")))
  _set_nested(updated, ["openai", "pixazo_model"], str(options.get("pixazo_model", "flux-schnell")))
  pixazo_api_key = options.get("pixazo_api_key")
  if isinstance(pixazo_api_key, str):
    pixazo_api_key = pixazo_api_key.strip()
    if pixazo_api_key and pixazo_api_key != MASKED_SECRET_VALUE:
      _set_nested(updated, ["openai", "pixazo_api_key"], pixazo_api_key)

  openai_enabled = bool(options.get("openai_enabled", True))
  music_detection_enabled = bool(options.get("music_detection_enabled", True))
  if persist_toggle_updates:
    write_toggle_state_updates({
      "openai_enabled": openai_enabled,
      "music_detection_enabled": music_detection_enabled,
    })
  api_key = options.get("openai_api_key")
  if openai_enabled and isinstance(api_key, str):
    api_key = api_key.strip()
    if api_key and api_key != MASKED_SECRET_VALUE:
      _set_nested(updated, ["openai", "api_key"], api_key)

  _set_nested(updated, ["orchestrator", "debounce_seconds"], int(options.get("debounce_seconds", 30)))
  _set_nested(updated, ["orchestrator", "cache_ttl_seconds"], int(options.get("cache_ttl_seconds", 86400)))
  _set_nested(updated, ["orchestrator", "cache_size"], int(options.get("cache_size", 512)))
  _set_nested(updated, ["log", "log_file_path"], str(options.get("log_file_path", "")))

  _set_nested(updated, ["lighting", "day"], str(options.get("lighting_day", "")))
  _set_nested(updated, ["lighting", "twilight"], str(options.get("lighting_twilight", "")))
  _set_nested(updated, ["lighting", "night"], str(options.get("lighting_night", "")))

  return updated


def write_config_data(config: Dict[str, Any]) -> None:
  SETTINGS_STORE.backup_database()
  SETTINGS_STORE.save_config(config)


def write_selected_orientation(orientation: str) -> None:
  orientation = (orientation or "portrait").lower()
  if orientation not in ("portrait", "landscape"):
    orientation = "portrait"

  write_toggle_state_updates({"orientation": orientation})


def write_toggle_state_updates(updates: Dict[str, Any]) -> None:
  SETTINGS_STORE.save_toggle_state(updates)


def build_cache_stats_payload() -> Dict[str, Any]:
  db_path = SETTINGS_STORE._database_path
  db_size = db_path.stat().st_size if db_path.exists() else 0

  weather_cache = SETTINGS_STORE.load_weather_cache()
  weather_fetched_at = None
  if isinstance(weather_cache, dict):
    raw_fetched_at = weather_cache.get("fetched_at")
    if isinstance(raw_fetched_at, str) and raw_fetched_at.strip():
      weather_fetched_at = raw_fetched_at

  return {
    "database_path": str(db_path),
    "database_size_bytes": int(db_size),
    "enrichment_cache": SETTINGS_STORE.enrichment_cache_stats(),
    "weather_cache": {
      "present": bool(weather_cache),
      "updated_at": SETTINGS_STORE.weather_cache_updated_at(),
      "fetched_at": weather_fetched_at,
    },
  }


def build_stream_payload(host_header: str, event_limit: int = 80) -> Dict[str, Any]:
  config = load_config_data()
  toggle = load_toggle_state()
  _ = host_header
  log_path = resolve_log_file_path(config)
  return {
    "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    "current_image": build_current_image_payload(config, toggle),
    "events": {
      "log_path": str(log_path),
      "events": read_recent_events(log_path, event_limit),
    },
    "cache_stats": build_cache_stats_payload(),
  }


class ConfigManagerHandler(BaseHTTPRequestHandler):
  def _ensure_authorized(self, path: str, query: Dict[str, list[str]]) -> bool:
    if not (path.startswith("/api") or path.startswith("/current-image")):
      return True

    config = load_config_data()
    expected = resolve_web_admin_token(config)
    if not expected:
      return True

    provided = (self.headers.get("X-Admin-Token") or "").strip()
    if not provided:
      provided = str((query.get("token", [""]) or [""])[0]).strip()

    if provided == expected:
      return True

    self._send_json(
      {"error": "Unauthorized. Provide X-Admin-Token header or token query parameter."},
      HTTPStatus.UNAUTHORIZED,
    )
    return False

  def _send_sse_event(self, event_type: str, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False)
    self.wfile.write(f"event: {event_type}\n".encode("utf-8"))
    self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
    self.wfile.flush()

  def _serve_event_stream(self) -> None:
    self.send_response(HTTPStatus.OK)
    self.send_header("Content-Type", "text/event-stream")
    self.send_header("Cache-Control", "no-cache")
    self.send_header("Connection", "keep-alive")
    self.send_header("X-Accel-Buffering", "no")
    self.end_headers()

    while True:
      payload = build_stream_payload(self.headers.get("Host", ""), event_limit=80)
      self._send_sse_event("snapshot", payload)
      time.sleep(5)

  def _send_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

  def _send_html(self, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

  def _send_file(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self._send_json({"error": f"Image file does not exist: {file_path}"}, HTTPStatus.NOT_FOUND)
            return

        mime, _ = mimetypes.guess_type(str(file_path))
        if not mime:
            mime = "application/octet-stream"

        content = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

  def _send_image_with_rotation(self, file_path: Path, rotate_degrees: int) -> None:
        if rotate_degrees % 360 == 0:
            self._send_file(file_path)
            return

        if not file_path.exists() or not file_path.is_file():
            self._send_json({"error": f"Image file does not exist: {file_path}"}, HTTPStatus.NOT_FOUND)
            return

        try:
            with Image.open(file_path) as img:
                rotated = img.rotate(rotate_degrees, expand=True)
                buf = BytesIO()
                rotated.save(buf, format="PNG")
                payload = buf.getvalue()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            # If rotation fails (for example while image is being rewritten),
            # serve the source file so the admin panel still has a live preview.
            try:
              self._send_file(file_path)
            except Exception:
              self._send_json({"error": f"Failed to rotate image preview: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

  def do_GET(self) -> None:
      parsed_url = urlsplit(self.path)
      path = parsed_url.path
      query = parse_qs(parsed_url.query)

      if not self._ensure_authorized(path, query):
        return

      if path == "/":
        self._send_html(HTML_PAGE)
        return

      if path == "/api/config":
        try:
          self._send_json({"config": load_config_data()})
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/config-options":
        try:
          config = load_config_data()
          self._send_json(build_config_options(config))
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/events":
        try:
          config = load_config_data()
          log_path = resolve_log_file_path(config)
          raw_limit = (query.get("limit", ["120"]) or ["120"])[0]
          try:
            limit = int(str(raw_limit))
          except (TypeError, ValueError):
            limit = 120
          self._send_json({
            "log_path": str(log_path),
            "events": read_recent_events(log_path, limit),
          })
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/debug-audio":
        try:
          config = load_config_data()
          raw_limit = (query.get("limit", ["30"]) or ["30"])[0]
          try:
            limit = int(str(raw_limit))
          except (TypeError, ValueError):
            limit = 30
          self._send_json(list_debug_audio_entries(config, limit))
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/debug-audio/file":
        try:
          config = load_config_data()
          requested_name = (query.get("name", [""]) or [""])[0]
          target_file = resolve_debug_audio_file(config, requested_name)
          self._send_file(target_file)
        except PermissionError as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
        except ValueError as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except FileNotFoundError:
          self._send_json({"error": "Debug audio file not found."}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/stream":
        try:
          self._serve_event_stream()
        except (BrokenPipeError, ConnectionResetError):
          return
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/cache-stats":
        try:
          self._send_json(build_cache_stats_payload())
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/app/status":
        try:
          self._send_json(get_main_service_status())
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/preview-prompt":
        try:
          config = load_config_data()
          self._send_json({"prompt": _build_preview_prompt(config)})
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/test-ai-image/file":
        try:
          self._send_file(TEST_AI_PREVIEW_PATH)
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/current-image":
        try:
          config = load_config_data()
          toggle = load_toggle_state()
          self._send_json(build_current_image_payload(config, toggle))
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path.startswith("/current-image"):
        try:
          config = load_config_data()
          toggle = load_toggle_state()
          image_payload = build_current_image_payload(config, toggle)
          image_path = Path(image_payload.get("selected_image_path", ""))
          rotate_degrees = int(image_payload.get("preview_rotation_degrees", 0))
          self._send_image_with_rotation(image_path, rotate_degrees)
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

  def do_POST(self) -> None:
      parsed_url = urlsplit(self.path)
      path = parsed_url.path
      query = parse_qs(parsed_url.query)

      if not self._ensure_authorized(path, query):
        return

      try:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_payload = self.rfile.read(content_length)
        payload = json.loads(raw_payload.decode("utf-8"))

        if path == "/api/validate-key":
          config = load_config_data()
          key_type = payload.get("key_type", "")
          raw_key_value = str(payload.get("key_value", "")).strip()
          use_stored_when_masked = bool(payload.get("use_stored_when_masked", False))

          key_value = raw_key_value
          if use_stored_when_masked and raw_key_value == MASKED_SECRET_VALUE:
            if key_type == "openai_key":
              openai_cfg = config.get("openai", {}) if isinstance(config.get("openai", {}), dict) else {}
              key_value = str(openai_cfg.get("api_key", "")).strip()
            elif key_type == "pixazo_key":
              openai_cfg = config.get("openai", {}) if isinstance(config.get("openai", {}), dict) else {}
              key_value = str(openai_cfg.get("pixazo_api_key", "")).strip()
            elif key_type == "openweather_key":
              weather_cfg = config.get("weather", {}) if isinstance(config.get("weather", {}), dict) else {}
              key_value = str(weather_cfg.get("openweathermap_api_key", "")).strip()
          if not key_value:
            self._send_json({"valid": False, "reason": "Empty key"})
            return

          valid = False
          reason = ""
          if key_type == "admin_token":
            valid = len(key_value) >= 8
            reason = "Admin token must be at least 8 characters" if not valid else "Admin token accepted"
          elif key_type == "openai_key":
            valid, reason = _validate_openai_api_key(key_value)
          elif key_type == "pixazo_key":
            valid, reason = _validate_pixazo_api_key(key_value)
          elif key_type == "openweather_key":
            valid, reason = _validate_openweather_api_key(key_value)
          else:
            valid = len(key_value) >= 8
            reason = "Value must be at least 8 characters" if not valid else "Value accepted"

          self._send_json({"valid": valid, "reason": reason})
          return

        if path == "/api/upload-fallback-image":
          if not isinstance(payload, dict):
            self._send_json({"error": "Request body must be an object."}, HTTPStatus.BAD_REQUEST)
            return

          target = str(payload.get("target", "")).strip()
          filename = str(payload.get("filename", "")).strip()
          content_base64 = payload.get("content_base64", "")
          saved_path = _save_uploaded_fallback_image(target, filename, content_base64)
          self._send_json({
            "message": "Fallback image uploaded.",
            "path": saved_path,
            "target": target,
          })
          return

        if path == "/api/fallback/use-current-generated":
          if not isinstance(payload, dict):
            self._send_json({"error": "Request body must be an object."}, HTTPStatus.BAD_REQUEST)
            return

          target = str(payload.get("target", "")).strip()
          config_key = FALLBACK_TARGET_TO_CONFIG_KEY.get(target)
          if not config_key:
            self._send_json({"error": "Unsupported fallback target."}, HTTPStatus.BAD_REQUEST)
            return

          try:
            config = load_config_data()
            saved_path = _save_current_generated_image_as_fallback(target, config)
            _set_nested(config, ["image", config_key], saved_path)
            write_config_data(config)
            self._send_json(
              {
                "message": "Fallback image updated from current generated image.",
                "target": target,
                "path": saved_path,
              }
            )
          except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
          except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
          return

        if path == "/api/preview-prompt":
          options = payload if isinstance(payload, dict) else {}
          preview_config = build_preview_config_from_options(options)
          self._send_json({"prompt": _build_preview_prompt(preview_config)})
          return

        if path == "/api/test-ai-image":
          options = payload if isinstance(payload, dict) else {}
          preview_config = build_preview_config_from_options(options)
          try:
            ai_service = AIBackgroundService(config_override=preview_config, output_override=str(TEST_AI_PREVIEW_PATH))
            result = ai_service.generate_test_image(str(TEST_AI_PREVIEW_PATH), prompt_override=_build_preview_prompt(preview_config))
            self._send_json({
              "message": f"Test image generated using {result.get('provider', 'openai')} ({result.get('model', '')}).",
              "prompt": result.get("prompt", ""),
              "size": result.get("size", ""),
            })
          except Exception as exc:
            openai_cfg = preview_config.get("openai", {}) if isinstance(preview_config.get("openai", {}), dict) else {}
            provider = str(openai_cfg.get("provider", "openai") or "openai")
            hint = (
              "Check Pixazo API key, selected model, and network connectivity."
              if provider == "pixazo"
              else "Check OpenAI API key, selected model, and account permissions."
            )
            error_text = str(exc).strip() or exc.__class__.__name__
            self._send_json(
              {
                "error": f"Test image generation failed: {error_text}",
                "provider": provider,
                "error_type": exc.__class__.__name__,
                "hint": hint,
              },
              HTTPStatus.BAD_GATEWAY,
            )
          return

        if path == "/api/debug-audio/delete":
          if not isinstance(payload, dict):
            self._send_json({"error": "Request body must be an object."}, HTTPStatus.BAD_REQUEST)
            return

          file_name = str(payload.get("name", "")).strip()
          if not file_name:
            self._send_json({"error": "Missing debug audio file name."}, HTTPStatus.BAD_REQUEST)
            return

          config = load_config_data()
          try:
            deleted = delete_debug_audio_file(config, file_name)
            self._send_json({"message": f"Deleted debug recording: {deleted.name}", "name": deleted.name})
          except PermissionError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
          except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
          except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
          return

        if path == "/api/debug-audio/delete-all":
          if not isinstance(payload, dict):
            self._send_json({"error": "Request body must be an object."}, HTTPStatus.BAD_REQUEST)
            return

          if payload.get("confirm") is not True:
            self._send_json({"error": "Confirmation required to delete all debug recordings."}, HTTPStatus.BAD_REQUEST)
            return

          config = load_config_data()
          result = delete_all_debug_audio_files(config)
          deleted_count = int(result.get("deleted_count", 0))
          failed_count = int(result.get("failed_count", 0))
          failed_names = result.get("failed_names", [])

          if failed_count > 0:
            self._send_json(
              {
                "error": f"Deleted {deleted_count} files, but {failed_count} could not be removed.",
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "failed_names": failed_names,
              },
              HTTPStatus.MULTI_STATUS,
            )
          else:
            self._send_json({"message": f"Deleted {deleted_count} debug recordings.", "deleted_count": deleted_count})
          return

        if path == "/api/events/clear":
          if not isinstance(payload, dict):
            self._send_json({"error": "Request body must be an object."}, HTTPStatus.BAD_REQUEST)
            return

          if payload.get("confirm") is not True:
            self._send_json({"error": "Confirmation required to clear the event log."}, HTTPStatus.BAD_REQUEST)
            return

          config = load_config_data()
          result = clear_event_log(config)
          self._send_json(
            {
              "message": f"Event log cleared ({result.get('cleared_bytes', 0)} bytes removed).",
              "log_path": result.get("log_path", ""),
              "cleared_bytes": result.get("cleared_bytes", 0),
            }
          )
          return

        if path == "/api/app/restart":
          if not isinstance(payload, dict):
            self._send_json({"error": "Request body must be an object."}, HTTPStatus.BAD_REQUEST)
            return

          if payload.get("confirm") is not True:
            self._send_json({"error": "Confirmation required to restart the app service."}, HTTPStatus.BAD_REQUEST)
            return

          try:
            self._send_json(restart_main_service())
          except Exception as exc:
            self._send_json(
              {
                "error": f"Failed to restart now-playing.service: {exc}",
                "hint": "Ensure sudoers allows this web service user to run systemctl restart now-playing.service without a password.",
              },
              HTTPStatus.BAD_GATEWAY,
            )
          return

        if path not in ("/api/config", "/api/config-options", "/api/preview-prompt", "/api/test-ai-image", "/api/upload-fallback-image", "/api/fallback/use-current-generated", "/api/debug-audio/delete", "/api/debug-audio/delete-all", "/api/events/clear", "/api/app/restart"):
          self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
          return

        if path == "/api/config-options":
          config = load_config_data()
          base_options = build_config_options(config)
          options = dict(base_options)
          if isinstance(payload, dict):
            options.update(payload)
          updated = apply_config_options(config, options)
          write_config_data(updated)
          write_selected_orientation(str(options.get("selected_orientation", "portrait")))
          self._send_json({"message": "Config options saved."})
          return

        config_payload = payload.get("config")
        if not isinstance(config_payload, dict):
          self._send_json({"error": "Request body must include object field 'config'."}, HTTPStatus.BAD_REQUEST)
          return

        backup_path = backup_config()
        SETTINGS_STORE.save_config(config_payload)

        self._send_json(
          {
            "message": "Config saved.",
            "backup_path": str(backup_path) if backup_path is not None else None,
          }
        )
      except json.JSONDecodeError:
        self._send_json({"error": "Invalid JSON payload."}, HTTPStatus.BAD_REQUEST)
      except Exception as exc:
        self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def run_server(host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), ConfigManagerHandler)
    print(f"Config web interface running at http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Now Playing config web manager")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8088, help="Port to bind")
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
