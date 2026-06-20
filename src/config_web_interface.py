import argparse
import json
import mimetypes
import re
import shutil
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
from spotipy.oauth2 import SpotifyOAuth

from settings_store import SettingsStore, SpotifyDbCacheHandler


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_STORE = SettingsStore()
MASKED_SECRET_VALUE = "********"


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
      <h2>Configuration Options</h2>
      <div class="content">
        <div class="section-title">General</div>
        <div class="grid">
          <div class="field"><label>Web Host</label><input id="webHost" /></div>
          <div class="field"><label>Web Port</label><input id="webPort" type="number" min="1" max="65535" /></div>
          <div class="field"><label>Admin API Token (leave blank to keep current)</label><input id="webAdminToken" type="password" /></div>
        </div>

        <label class="checkbox-row"><input id="webEnabled" type="checkbox" /> Enable web interface</label>
        <label class="checkbox-row"><input id="musicDetectionEnabled" type="checkbox" /> Enable music detection and lookup</label>
        <label class="checkbox-row"><input id="openaiEnabled" type="checkbox" /> Enable OpenAI background generation</label>

        <div class="section-title">Audio</div>
        <div class="grid">
          <div class="field"><label>Recording Duration Seconds</label><input id="audioDuration" type="number" min="1" max="30" /></div>
          <div class="field"><label>Audio Gain (dB)</label><input id="audioGainDb" type="range" min="-20" max="20" step="0.1" /></div>
        </div>

        <label class="checkbox-row"><input id="debugAudioEnabled" type="checkbox" /> Enable debug audio capture</label>

        <div id="openaiSectionTitle" class="section-title hidden">OpenAI</div>
        <div id="openaiSettings" class="hidden">
          <div class="subsection">
            <div class="section-title">Generation</div>
            <div class="grid">
              <div class="field"><label>OpenAI Model</label><input id="openaiModel" /></div>
              <div class="field"><label>OpenAI Prompt Style</label><input id="openaiPromptStyle" /></div>
              <div class="field"><label>OpenAI API Key (leave blank to keep current)</label><input id="openaiApiKey" type="password" /></div>
            </div>
          </div>
          <div class="subsection">
            <div class="section-title">Weather & AI Dot</div>
            <div class="grid">
              <div class="field"><label>Weather Refresh Seconds</label><input id="weatherRefresh" type="number" min="60" /></div>
              <div class="field"><label>Timezone</label><input id="weatherTimezone" /></div>
              <div class="field"><label>Weather Background Image</label><input id="weatherBg" /></div>
              <div class="field"><label>AI Dot Margin X Px</label><input id="aiDotMarginXPx" type="number" min="0" /></div>
              <div class="field"><label>AI Dot Margin Y Px</label><input id="aiDotMarginYPx" type="number" min="0" /></div>
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
        </div>

        <div id="debugAudioSettings" class="hidden">
          <div class="section-title">Debug Audio</div>
          <div class="grid">
            <div class="field"><label>Debug Audio Path</label><input id="debugAudioPath" /></div>
          </div>
          <div class="row"><button id="refreshDebugAudioBtn" type="button">Refresh Debug Audio</button></div>
          <div id="debugAudioMeta" class="debug-audio-meta">Debug audio list unavailable.</div>
          <div id="debugAudioList" class="debug-audio-list"></div>
          <audio id="debugAudioPlayer" class="debug-audio-player" controls preload="none"></audio>
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
          <div class="field"><label>Legacy Fallback Image</label><input id="fallbackImagePath" /></div>
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
            <div class="field"><label>Fallback Day Portrait</label><input id="fallbackDayPortrait" /></div>
            <div class="field"><label>Fallback Night Portrait</label><input id="fallbackNightPortrait" /></div>
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
            <div class="field"><label>Fallback Day Landscape</label><input id="fallbackDayLandscape" /></div>
            <div class="field"><label>Fallback Night Landscape</label><input id="fallbackNightLandscape" /></div>
          </div>
        </div>

        <div class="section-title">Weather & Integrations</div>
        <div class="grid">
          <div class="field"><label>OpenWeather API Key</label><input id="weatherApiKey" type="password" /></div>
          <div class="field"><label>Geo Coordinates</label><input id="geoCoordinates" /></div>
          <div class="field"><label>Spotify Client ID</label><input id="spotifyClientId" /></div>
          <div class="field"><label>Spotify Client Secret</label><input id="spotifyClientSecret" type="password" /></div>
        </div>

        <div class="subsection">
          <div class="section-title">Spotify Authorization</div>
          <div class="row">
            <button type="button" id="spotifyAuthStartBtn">Open Spotify Login</button>
            <button type="button" class="secondary" id="spotifyAuthStatusBtn">Refresh Spotify Status</button>
          </div>
          <div id="spotifyAuthStatus" class="meta">Loading Spotify authorization status...</div>
        </div>

        <div class="section-title">Processing & Logs</div>
        <div class="grid">
          <div class="field"><label>Debounce Seconds</label><input id="debounceSeconds" type="number" min="0" /></div>
          <div class="field"><label>Cache TTL Seconds</label><input id="cacheTtlSeconds" type="number" min="0" /></div>
          <div class="field"><label>Cache Size</label><input id="cacheSize" type="number" min="0" /></div>
          <div class="field"><label>Log File Path</label><input id="logFilePath" /></div>
        </div>

        <div class="row">
          <button id="saveOptionsBtn">Save Options</button>
          <button class="secondary" id="reloadBtn">Reload</button>
        </div>
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
        <div class="row"><button id="refreshEventsBtn">Refresh Events</button></div>
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

  <script>
    const statusEl = document.getElementById("status");
    const metaEl = document.getElementById("meta");
    const imageBox = document.getElementById("imageBox");
    const eventMetaEl = document.getElementById("eventMeta");
    const eventLogEl = document.getElementById("eventLog");
    const spotifyAuthStatusEl = document.getElementById("spotifyAuthStatus");
    const cacheStatsEl = document.getElementById("cacheStats");
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
      spotifyClientId: document.getElementById("spotifyClientId"),
      spotifyClientSecret: document.getElementById("spotifyClientSecret"),
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
    const debugAudioSettings = document.getElementById("debugAudioSettings");
    const portraitSettings = document.getElementById("portraitSettings");
    const landscapeSettings = document.getElementById("landscapeSettings");
    const MASKED_SECRET_VALUE = "********";
    const tabPanels = new Map();
    const tabButtons = new Map();
    let activateTabFn = null;
    let portalToken = "";
    let eventSource = null;

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

      debugAudioSettings.classList.toggle("hidden", !fields.debugAudioEnabled.checked);
      const orientation = fields.selectedOrientation.value || "portrait";
      portraitSettings.classList.toggle("hidden", orientation !== "portrait");
      landscapeSettings.classList.toggle("hidden", orientation !== "landscape");
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
        { key: "openai", title: "OpenAI" },
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
      fields.spotifyClientId.value = data.spotify_client_id || "";
      fields.spotifyClientSecret.value = data.spotify_client_secret_configured ? MASKED_SECRET_VALUE : "";
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
        "OpenAI Model": "Image model used for AI background generation. Example: gpt-image-1-mini for lower cost/faster generation.",
        "OpenAI Prompt Style": "Creative style appended to prompts. Example: moody watercolor cityscape, retro synthwave sunset, minimalist ink wash.",
        "OpenAI API Key (leave blank to keep current)": "Secret key for OpenAI requests. Leave as ******** to keep current value, or paste a new key to rotate.",
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
        "Spotify Client ID": "Spotify app client ID for enrichment/auth flows.",
        "Spotify Client Secret": "Spotify app secret. Leave as ******** to keep current value, or paste a new secret.",
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
        "Enable OpenAI background generation": "Enables AI image generation; when off, the app uses configured static fallback images.",
        "Enable debug audio capture": "Saves captured audio clips for troubleshooting detection/identify behavior.",
      };

      const buttonHints = {
        "Save Options": "Persists all current settings to the database.",
        "Reload": "Re-reads settings from the database and refreshes the form.",
        "Refresh Image State": "Fetches the latest selected/preview image metadata and updates preview pane.",
        "Refresh Events": "Loads recent service log events.",
        "Refresh Debug Audio": "Loads recent debug audio recordings from the configured debug audio folder.",
        "Open Spotify Login": "Starts Spotify OAuth in a new tab.",
        "Refresh Spotify Status": "Checks whether a Spotify token exists and is still valid.",
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

    function buildDebugAudioUrl(fileName) {
      const params = new URLSearchParams({ name: fileName });
      if (portalToken) {
        params.set("token", portalToken);
      }
      return '/api/debug-audio/file?' + params.toString();
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
        const size = escapeHtml(formatBytes(row.size_bytes));
        const modified = escapeHtml(row.modified_at || "");
        return (
          '<div class="debug-audio-row">' +
            '<div>' +
              '<div class="debug-audio-name">' + fileName + '</div>' +
              '<div class="debug-audio-details">' + size + ' • ' + modified + '</div>' +
            '</div>' +
            '<button type="button" class="secondary debug-audio-play-btn" data-name="' + fileName + '">Play</button>' +
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
        spotify_client_id: fields.spotifyClientId.value.trim(),
        spotify_client_secret: fields.spotifyClientSecret.value,
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
      await loadSpotifyAuthStatus();
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

    function formatSpotifyAuthStatus(data) {
      const lines = [];
      lines.push(`Configured: ${data.configured ? "yes" : "no"}`);
      lines.push(`Token cached: ${data.has_token ? "yes" : "no"}`);
      if (data.updated_at) {
        lines.push(`Updated at: ${data.updated_at}`);
      }
      if (typeof data.expires_at === "number") {
        lines.push(`Expires at: ${new Date(data.expires_at * 1000).toLocaleString()}`);
      }
      lines.push(`Valid: ${data.is_valid ? "yes" : "no"}`);
      if (data.redirect_uri) {
        lines.push(`Redirect URI: ${data.redirect_uri}`);
      }
      return lines.join("\\n");
    }

    async function loadSpotifyAuthStatus() {
      try {
        const res = await apiFetch('/api/spotify-auth/status');
        const data = await res.json();
        if (!res.ok) {
          spotifyAuthStatusEl.textContent = data.error || 'Failed to load Spotify auth status.';
          return;
        }
        spotifyAuthStatusEl.textContent = formatSpotifyAuthStatus(data);
      } catch (error) {
        spotifyAuthStatusEl.textContent = `Failed to load Spotify auth status: ${error}`;
      }
    }

    async function openSpotifyLogin() {
      setStatus("Preparing Spotify login...", true);
      try {
        const res = await apiFetch('/api/spotify-auth/start');
        const data = await res.json();
        if (!res.ok) {
          setStatus(data.error || 'Failed to create Spotify login URL.', false);
          return;
        }
        if (data.authorize_url) {
          window.open(data.authorize_url, '_blank', 'noopener,noreferrer');
          setStatus('Spotify login opened in a new tab.', true);
          await loadSpotifyAuthStatus();
        } else {
          setStatus('Spotify login URL was not returned.', false);
        }
      } catch (error) {
        setStatus(`Failed to start Spotify login: ${error}`, false);
      }
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

      const spotify = data.spotify_auth_cache || {};
      lines.push(`Spotify auth token present: ${spotify.present ? "yes" : "no"}`);
      if (spotify.updated_at) {
        lines.push(`Spotify token updated at: ${spotify.updated_at}`);
      }
      if (typeof spotify.expires_at === "number") {
        lines.push(`Spotify token expires at: ${new Date(spotify.expires_at * 1000).toLocaleString()}`);
      }
      lines.push(`Spotify token valid: ${spotify.is_valid ? "yes" : "no"}`);

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

      if (snapshot.spotify_auth) {
        spotifyAuthStatusEl.textContent = formatSpotifyAuthStatus(snapshot.spotify_auth);
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
    });
    document.getElementById("refreshImageBtn").addEventListener("click", loadImageState);
    document.getElementById("refreshEventsBtn").addEventListener("click", loadEvents);
    document.getElementById("refreshDebugAudioBtn").addEventListener("click", loadDebugAudioList);
    document.getElementById("spotifyAuthStartBtn").addEventListener("click", openSpotifyLogin);
    document.getElementById("spotifyAuthStatusBtn").addEventListener("click", loadSpotifyAuthStatus);
    document.getElementById("refreshCacheStatsBtn").addEventListener("click", loadCacheStats);

    debugAudioListEl.addEventListener("click", (evt) => {
      const target = evt.target;
      if (!(target instanceof HTMLElement) || !target.classList.contains("debug-audio-play-btn")) {
        return;
      }
      const fileName = target.getAttribute("data-name") || "";
      if (!fileName) {
        return;
      }
      debugAudioPlayerEl.src = buildDebugAudioUrl(fileName);
      debugAudioPlayerEl.play().catch(() => {});
    });

    (async () => {
      setupSectionTabs();
      applyHoverHints();
      await loadOptions();
      await loadImageState();
      await loadEvents();
      await loadDebugAudioList();
      await loadSpotifyAuthStatus();
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
    return {
      "selected_image_path": str(image_path),
      "image_exists": image_path.exists(),
      "orientation": str(toggle.get("orientation", "portrait")).lower(),
      "ai_bg_fallback_mode": bool(toggle.get("ai_bg_fallback_mode", False)),
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


def build_config_options(config: Dict[str, Any]) -> Dict[str, Any]:
    display_cfg = config.get("display", {}) if isinstance(config.get("display", {}), dict) else {}
    weather_cfg = config.get("weather", {}) if isinstance(config.get("weather", {}), dict) else {}
    image_cfg = config.get("image", {}) if isinstance(config.get("image", {}), dict) else {}
    web_cfg = config.get("web_interface", {}) if isinstance(config.get("web_interface", {}), dict) else {}
    openai_cfg = config.get("openai", {}) if isinstance(config.get("openai", {}), dict) else {}
    audio_cfg = config.get("audio", {}) if isinstance(config.get("audio", {}), dict) else {}
    spotify_cfg = config.get("spotify", {}) if isinstance(config.get("spotify", {}), dict) else {}
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
        "spotify_client_id": str(spotify_cfg.get("client_id", "")),
        "spotify_client_secret": "",
        "spotify_client_secret_configured": bool(str(spotify_cfg.get("client_secret", "")).strip()),
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


def apply_config_options(config: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
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

  _set_nested(updated, ["display", "text_alignment_portrait"], str(options.get("text_alignment_portrait", "left")))
  _set_nested(updated, ["display", "text_alignment_landscape"], str(options.get("text_alignment_landscape", "left")))
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

  openai_enabled = bool(options.get("openai_enabled", True))
  music_detection_enabled = bool(options.get("music_detection_enabled", True))
  write_toggle_state_updates({
    "openai_enabled": openai_enabled,
    "music_detection_enabled": music_detection_enabled,
  })
  api_key = options.get("openai_api_key")
  if openai_enabled and isinstance(api_key, str):
    api_key = api_key.strip()
    if api_key and api_key != MASKED_SECRET_VALUE:
      _set_nested(updated, ["openai", "api_key"], api_key)

  _set_nested(updated, ["spotify", "client_id"], str(options.get("spotify_client_id", "")))
  spotify_client_secret = options.get("spotify_client_secret")
  if isinstance(spotify_client_secret, str):
    spotify_client_secret = spotify_client_secret.strip()
    if spotify_client_secret and spotify_client_secret != MASKED_SECRET_VALUE:
      _set_nested(updated, ["spotify", "client_secret"], spotify_client_secret)

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


def _build_spotify_redirect_uri(host_header: str) -> str:
  host = (host_header or "").strip()
  if not host:
    raise ValueError("Missing Host header for Spotify redirect URI.")
  return f"http://{host}/api/spotify-auth/callback"


def _build_spotify_oauth(config: Dict[str, Any], redirect_uri: str) -> SpotifyOAuth:
  spotify_cfg = config.get("spotify", {}) if isinstance(config.get("spotify", {}), dict) else {}
  client_id = str(spotify_cfg.get("client_id", "")).strip()
  client_secret = str(spotify_cfg.get("client_secret", "")).strip()
  if not client_id or not client_secret:
    raise ValueError("Spotify client ID and client secret must be set before authorizing.")

  return SpotifyOAuth(
    client_id=client_id,
    client_secret=client_secret,
    redirect_uri=redirect_uri,
    scope="",
    open_browser=False,
    cache_handler=SpotifyDbCacheHandler(SETTINGS_STORE),
  )


def build_spotify_auth_status(config: Dict[str, Any], redirect_uri: str) -> Dict[str, Any]:
  spotify_cfg = config.get("spotify", {}) if isinstance(config.get("spotify", {}), dict) else {}
  client_id = str(spotify_cfg.get("client_id", "")).strip()
  client_secret = str(spotify_cfg.get("client_secret", "")).strip()
  token_info = SETTINGS_STORE.load_spotify_auth_token()
  expires_at = token_info.get("expires_at") if isinstance(token_info, dict) else None
  now_seconds = int(datetime.utcnow().timestamp())
  is_valid = isinstance(expires_at, int) and expires_at > now_seconds

  return {
    "configured": bool(client_id and client_secret),
    "has_token": bool(token_info),
    "updated_at": SETTINGS_STORE.spotify_auth_token_updated_at(),
    "expires_at": expires_at if isinstance(expires_at, int) else None,
    "is_valid": is_valid,
    "redirect_uri": redirect_uri,
  }


def build_cache_stats_payload() -> Dict[str, Any]:
  db_path = SETTINGS_STORE._database_path
  db_size = db_path.stat().st_size if db_path.exists() else 0

  weather_cache = SETTINGS_STORE.load_weather_cache()
  weather_fetched_at = None
  if isinstance(weather_cache, dict):
    raw_fetched_at = weather_cache.get("fetched_at")
    if isinstance(raw_fetched_at, str) and raw_fetched_at.strip():
      weather_fetched_at = raw_fetched_at

  spotify_token = SETTINGS_STORE.load_spotify_auth_token()
  expires_at = spotify_token.get("expires_at") if isinstance(spotify_token, dict) else None
  now_seconds = int(datetime.utcnow().timestamp())

  return {
    "database_path": str(db_path),
    "database_size_bytes": int(db_size),
    "enrichment_cache": SETTINGS_STORE.enrichment_cache_stats(),
    "weather_cache": {
      "present": bool(weather_cache),
      "updated_at": SETTINGS_STORE.weather_cache_updated_at(),
      "fetched_at": weather_fetched_at,
    },
    "spotify_auth_cache": {
      "present": bool(spotify_token),
      "updated_at": SETTINGS_STORE.spotify_auth_token_updated_at(),
      "expires_at": expires_at if isinstance(expires_at, int) else None,
      "is_valid": isinstance(expires_at, int) and expires_at > now_seconds,
    },
  }


def build_stream_payload(host_header: str, event_limit: int = 80) -> Dict[str, Any]:
  config = load_config_data()
  toggle = load_toggle_state()
  redirect_uri = _build_spotify_redirect_uri(host_header)
  log_path = resolve_log_file_path(config)
  return {
    "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    "current_image": build_current_image_payload(config, toggle),
    "events": {
      "log_path": str(log_path),
      "events": read_recent_events(log_path, event_limit),
    },
    "cache_stats": build_cache_stats_payload(),
    "spotify_auth": build_spotify_auth_status(config, redirect_uri),
  }


class ConfigManagerHandler(BaseHTTPRequestHandler):
  def _ensure_authorized(self, path: str, query: Dict[str, list[str]]) -> bool:
    if not (path.startswith("/api") or path.startswith("/current-image")):
      return True

    if path == "/api/spotify-auth/callback":
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
          requested = query.get("limit", ["120"])[0]
          events = read_recent_events(log_path, int(requested))
          payload = {
            "log_path": str(log_path),
            "events": events,
          }
          self._send_json(payload)
        except ValueError:
          self._send_json({"error": "Invalid 'limit' query value."}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/debug-audio":
        try:
          config = load_config_data()
          requested = query.get("limit", ["30"])[0]
          self._send_json(list_debug_audio_entries(config, int(requested)))
        except ValueError:
          self._send_json({"error": "Invalid debug audio request."}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/debug-audio/file":
        try:
          config = load_config_data()
          name = str((query.get("name", [""]) or [""])[0]).strip()
          file_path = resolve_debug_audio_file(config, name)
          self._send_file(file_path)
        except ValueError as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except PermissionError as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
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

      if path == "/api/spotify-auth/status":
        try:
          config = load_config_data()
          redirect_uri = _build_spotify_redirect_uri(self.headers.get("Host", ""))
          self._send_json(build_spotify_auth_status(config, redirect_uri))
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return

      if path == "/api/spotify-auth/start":
        try:
          config = load_config_data()
          redirect_uri = _build_spotify_redirect_uri(self.headers.get("Host", ""))
          oauth = _build_spotify_oauth(config, redirect_uri)
          self._send_json({
            "authorize_url": oauth.get_authorize_url(),
            "redirect_uri": redirect_uri,
          })
        except Exception as exc:
          self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return

      if path == "/api/spotify-auth/callback":
        try:
          config = load_config_data()
          redirect_uri = _build_spotify_redirect_uri(self.headers.get("Host", ""))
          error = query.get("error", [None])[0]
          if error:
            self._send_html(f"<html><body><h1>Spotify authorization failed</h1><p>{error}</p><p><a href='/'>Return to portal</a></p></body></html>")
            return

          code = query.get("code", [None])[0]
          if not code:
            self._send_json({"error": "Missing Spotify authorization code."}, HTTPStatus.BAD_REQUEST)
            return

          oauth = _build_spotify_oauth(config, redirect_uri)
          oauth.get_access_token(code=code, as_dict=True, check_cache=False)
          self._send_html(
            "<html><body><h1>Spotify authorization complete</h1><p>The token has been saved to the database.</p><p><a href='/'>Return to the portal</a></p></body></html>"
          )
        except Exception as exc:
          self._send_html(
            f"<html><body><h1>Spotify authorization failed</h1><p>{exc}</p><p><a href='/'>Return to the portal</a></p></body></html>"
          )
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

      if path not in ("/api/config", "/api/config-options"):
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        return

      try:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_payload = self.rfile.read(content_length)
        payload = json.loads(raw_payload.decode("utf-8"))

        if path == "/api/config-options":
          config = load_config_data()
          options = payload if isinstance(payload, dict) else {}
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
        if backup_path is not None:
          _ = backup_path
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
