
    const statusEl = document.getElementById("status");
    const metaEl = document.getElementById("meta");
    const imageBox = document.getElementById("imageBox");
    const eventMetaEl = document.getElementById("eventMeta");
    const eventLogEl = document.getElementById("eventLog");
    const spotifyAuthStatusEl = document.getElementById("spotifyAuthStatus");
    const cacheStatsEl = document.getElementById("cacheStats");

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
    const debugAudioSettings = document.getElementById("debugAudioSettings");
    const portraitSettings = document.getElementById("portraitSettings");
    const landscapeSettings = document.getElementById("landscapeSettings");
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
      openaiSettings.classList.toggle("hidden", !fields.openaiEnabled.checked);
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
        { key: "display", title: "Display & Image" },
        { key: "orientation", title: "Orientation-Specific" },
        { key: "weather", title: "Weather & Integrations" },
        { key: "processing", title: "Processing & Logs" },
        { key: "lighting", title: "Lighting Presets" }
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

      const panels = new Map();
      const buttons = new Map();

      function activateTab(key) {
        for (const [panelKey, panel] of panels.entries()) {
          panel.classList.toggle("hidden", panelKey !== key);
        }
        for (const [buttonKey, button] of buttons.entries()) {
          button.classList.toggle("active", buttonKey === key);
        }
        try {
          window.localStorage.setItem("nowPlayingConfigTab", key);
        } catch (error) {
          void error;
        }
      }

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

        panels.set(section.key, panel);

        const button = document.createElement("button");
        button.type = "button";
        button.className = "tab-button";
        button.textContent = section.title;
        button.addEventListener("click", () => activateTab(section.key));
        if (index === 0) {
          button.classList.add("active");
        }
        buttons.set(section.key, button);
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
      if (panels.has(savedTab)) {
        activateTab(savedTab);
      }

      content.dataset.tabsReady = "true";
    }

    function applyOptionValues(data) {
      fields.webEnabled.checked = !!data.web_enabled;
      fields.webHost.value = data.web_host || "";
      fields.webPort.value = data.web_port ?? 8088;
      fields.webAdminToken.value = "";
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
      fields.openaiModel.value = data.openai_model || "";
      fields.openaiPromptStyle.value = data.openai_prompt_style || "";
      fields.openaiApiKey.value = "";
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
      fields.weatherApiKey.value = data.weather_api_key || "";
      fields.geoCoordinates.value = data.geo_coordinates || "";
      fields.spotifyClientId.value = data.spotify_client_id || "";
      fields.spotifyClientSecret.value = data.spotify_client_secret || "";
      fields.debounceSeconds.value = data.debounce_seconds ?? 30;
      fields.cacheTtlSeconds.value = data.cache_ttl_seconds ?? 86400;
      fields.cacheSize.value = data.cache_size ?? 512;
      fields.logFilePath.value = data.log_file_path || "";
      fields.lightingDay.value = data.lighting_day || "";
      fields.lightingTwilight.value = data.lighting_twilight || "";
      fields.lightingNight.value = data.lighting_night || "";
      applyVisibilityRules();
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
      return lines.join("\n");
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

      return lines.join("\n");
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
    document.getElementById("spotifyAuthStartBtn").addEventListener("click", openSpotifyLogin);
    document.getElementById("spotifyAuthStatusBtn").addEventListener("click", loadSpotifyAuthStatus);
    document.getElementById("refreshCacheStatsBtn").addEventListener("click", loadCacheStats);

    (async () => {
      setupSectionTabs();
      await loadOptions();
      await loadImageState();
      await loadEvents();
      await loadSpotifyAuthStatus();
      await loadCacheStats();
      startEventStream();
    })();
  