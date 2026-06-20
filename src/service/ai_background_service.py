
import base64
import datetime
import logging
import os
import shutil
from pathlib import Path
from typing import Literal, Optional, Tuple, cast

import requests  # for city name & weather description when needed
import traceback
import json

from logger import Logger
from config import Config
from util import Util  # to parse geo_coordinates
from openai import OpenAI  # OpenAI client for Images API

# Optional astro imports (fail-soft if not present)
try:
    # NOTE: These imports match Astral v2+ API
    from astral import Observer
    from astral.sun import azimuth as sun_azimuth, elevation as sun_elevation
    from astral.moon import phase as moon_phase
    _ASTRAL_OK = True
except Exception:
    _ASTRAL_OK = False


OpenAIImageSize = Literal[
    "auto",
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "256x256",
    "512x512",
    "1792x1024",
    "1024x1792",
]


class AIBackgroundService:

    def __init__(self, config_override: Optional[dict] = None, output_override: Optional[str] = None) -> None:
        self._logger: logging.Logger = Logger().get_logger()
        self._config: dict = config_override if isinstance(config_override, dict) else Config().get_config()

        # Output path (same as your DisplayService reads)
        self._outfile = (
            output_override
            or self._config.get("display", {}).get("weather_background_image")
            or "resources/ai_screensaver.png"
        )

        # Image refresh cadence
        self._refresh_seconds = int(
            self._config.get("weather", {}).get("background_refresh_seconds", 6 * 3600)
        )

        # Style
        self._prompt_style = (self._config.get("openai", {}) or {}).get(
            "prompt_style", "80s anime style"
        ).strip()

        # Coordinates: track validity explicitly
        self._lat: Optional[float] = None
        self._lon: Optional[float] = None
        self._coords_ok: bool = False
        geo_str = (self._config.get("weather", {}) or {}).get("geo_coordinates", "")
        try:
            self._lat, self._lon = Util.parse_coordinates(geo_str)
            self._coords_ok = True
        except Exception:
            self._coords_ok = False
            self._logger.warning(
                "Invalid geo_coordinates in config; disabling generation and using fallback."
            )

        # OpenAI & image setup
        openai_cfg = self._config.get("openai", {}) or {}
        image_cfg = self._config.get("image", {}) or {}
        lighting_cfg = self._config.get("lighting", {}) or {}
        self._provider = str(openai_cfg.get("provider") or "openai").strip().lower()
        self._pixazo_model = str(openai_cfg.get("pixazo_model") or "flux-schnell").strip()

        self._openai_api_key = openai_cfg.get("api_key") or os.environ.get("OPENAI_API_KEY") or ""
        self._pixazo_api_key = openai_cfg.get("pixazo_api_key") or os.environ.get("PIXAZO_API_KEY") or ""

        # Legacy single-path fallback (used when orientation-aware images are not configured)
        self._fallback_image_path = image_cfg.get("fallback_image_path") or ""

        # Optional orientation-aware fallbacks (portrait/landscape for day/night)
        # If present, these take precedence over generic day/night fallbacks.
        self._fallback_image_path_day_portrait = image_cfg.get("fallback_image_path_day_portrait") or ""
        self._fallback_image_path_day_landscape = image_cfg.get("fallback_image_path_day_landscape") or ""
        self._fallback_image_path_night_portrait = image_cfg.get("fallback_image_path_night_portrait") or ""
        self._fallback_image_path_night_landscape = image_cfg.get("fallback_image_path_night_landscape") or ""

        # Lighting strings from config (day/twilight/night). Keep default hard-coded fallback.
        self._lighting_cfg = lighting_cfg

        # chosen OpenAI image model (configurable)
        self._image_model = openai_cfg.get("model") or openai_cfg.get("image_model") or "gpt-image-1"

        # model-specific image settings (can be provided in config under openai or image)
        self._model_image_settings = openai_cfg.get("model_image_settings") or image_cfg.get("model_image_settings") or {}

        # display size (used to prefer non-square when supported)
        disp_cfg = self._config.get("display", {}) or {}
        self._display_width = int(disp_cfg.get("width", 800))
        self._display_height = int(disp_cfg.get("height", 480))
        self._display_orientation = (disp_cfg.get("orientation") or "").lower() or "portrait"

        # image config controls
        self._orientation_strategy = (image_cfg.get("orientation_strategy") or (self._config.get("image", {}) or {}).get("orientation_strategy") or "cover").lower()
        # maximum allowed dimension for generated images (caps model requests)
        self._max_image_dimension = int((image_cfg.get("max_dimension") or (self._config.get("image", {}) or {}).get("max_dimension") or 2048))
        # square size for square-only models (DALL·E fallback)
        self._max_square_size = int((image_cfg.get("max_square_size") or (self._config.get("image", {}) or {}).get("max_square_size") or 1024))

        self._client: Optional[OpenAI] = None
        self._last_refresh: Optional[datetime.datetime] = None

        # Cached per-refresh context (to avoid duplicate decisions/network calls)
        self._weather_cache: Optional[dict] = None
        self._weather_cache_timestamp: Optional[datetime.datetime] = None
        self._weather_cache_ttl_seconds = int(
            (self._config.get("weather", {}) or {}).get("weather_cache_ttl_seconds", 3600)
        )  # Default 1 hour
        self._city: Optional[str] = None
        self._weather_desc: Optional[str] = None
        self._astro_text: Optional[str] = None
        self._astro_text_timestamp: Optional[datetime.datetime] = None
        self._astro_text_ttl_seconds = 60  # Refresh every 1 minute to keep time-of-day accurate
        self._lighting_text: Optional[str] = None
        self._lighting_text_timestamp: Optional[datetime.datetime] = None
        self._lighting_text_ttl_seconds = 60  # Refresh every 1 minute to keep time-of-day accurate
        self._image_size: Optional[str] = None
        self._model_info: Optional[dict] = None

        if self._openai_api_key:
            try:
                self._client = OpenAI(api_key=self._openai_api_key)
            except Exception as e:
                self._logger.error(f"Failed to initialize OpenAI client: {e}")
        elif self._provider == "openai":
            self._logger.warning(
                "OpenAI API key missing. AI background generation will use fallback."
            )
        elif self._provider == "pixazo" and not self._pixazo_api_key:
            self._logger.warning(
                "Pixazo API key missing. AI background generation will use fallback."
            )
        # Log OpenAI-related configuration (do NOT log API key)
        try:
            self._logger.debug(
                "AI image configuration: provider=%s model=%s pixazo_model=%s client_initialized=%s max_image_dimension=%s max_square_size=%s",
                self._provider,
                self._image_model,
                self._pixazo_model,
                bool(self._client),
                self._max_image_dimension,
                self._max_square_size,
            )
        except Exception:
            pass

        # optional timezone fallback from config (e.g., "Europe/London") used when OpenWeather fails
        self._timezone_fallback = (self._config.get("weather", {}) or {}).get("timezone")

        # Try to import zoneinfo for timezone-aware fallbacks (Python 3.9+)
        try:
            from zoneinfo import ZoneInfo  # type: ignore
            self._ZoneInfo = ZoneInfo
        except Exception:
            self._ZoneInfo = None

    def _get_current_orientation(self) -> str:
        """
        Return the current orientation from the shared settings database.
        Falls back to the display config's orientation or "portrait".
        """
        orientation = (self._display_orientation or "portrait").lower()
        try:
            from settings_store import SettingsStore

            data = SettingsStore().load_toggle_state()
            o = (data.get('orientation') or orientation or 'portrait').lower()
            if o in ("portrait", "landscape"):
                try:
                    self._logger.debug("Orientation from settings database: %s", o)
                except Exception:
                    pass
                return o
        except Exception as e:
            try:
                self._logger.debug("Orientation read failed; using default '%s': %s", orientation, e)
            except Exception:
                pass
        return orientation

    def _should_refresh(self) -> bool:
        """
        Decide if we should attempt a refresh based on TTL only.
        (Do NOT gate on OpenAI client presence, so fallback can still run.)
        """
        if not self._last_refresh:
            return True
        age = (datetime.datetime.now() - self._last_refresh).total_seconds()
        return age >= self._refresh_seconds

    # --- helper to fetch city name + weather description using OpenWeather
    def _fetch_city_and_weather_desc(self) -> Tuple[str, str]:
        """
        Returns (city_name, weather_description).
        Uses OpenWeather key + units=metric, reusing your coords.
        Safe if coords invalid: returns generic strings without network call.
        """
        # Use cached weather if present
        if self._weather_cache:
            city = self._weather_cache.get("city") or "the nearest major city"
            desc = self._weather_cache.get("weather_desc") or "current local conditions"
            return city, desc

        if not self._coords_ok:
            return "the nearest major city", "current local conditions"

        # fetch and cache weather data for reuse by other helpers
        data = self._fetch_weather_data()
        if not data:
            return "the nearest major city", "current local conditions"
        city = data.get("city") or "the nearest major city"
        desc = data.get("weather_desc") or "current local conditions"
        return city, desc

    # --- helper to compute sun/moon context string
    def _sun_moon_context(self) -> str:
        """
        Returns a natural language snippet about sun or moon at current time & coords.
        Daytime -> sun azimuth/elevation; Night -> moon phase (as percentage).
        Fallbacks gracefully if astral isn't available or coords invalid.
        """
        # Use cached results only if still fresh (within TTL)
        if self._astro_text and self._astro_text_timestamp:
            age = (datetime.datetime.now() - self._astro_text_timestamp).total_seconds()
            if age < self._astro_text_ttl_seconds:
                return self._astro_text

        if not self._coords_ok:
            local_time = self._get_local_time_str()
            return f"Include the current sun/moon position in the sky appropriate for ({local_time})."

        now_utc = datetime.datetime.now(datetime.timezone.utc)

        # Use cached weather if present, otherwise try to fetch
        if not self._weather_cache:
            self._fetch_weather_data()

        try:
            sys_info = (self._weather_cache or {}).get("sys", {}) or {}
            sunrise_ts = sys_info.get("sunrise")
            sunset_ts = sys_info.get("sunset")
            if sunrise_ts and sunset_ts:
                sunrise_utc = datetime.datetime.utcfromtimestamp(sunrise_ts).replace(tzinfo=datetime.timezone.utc)
                sunset_utc = datetime.datetime.utcfromtimestamp(sunset_ts).replace(tzinfo=datetime.timezone.utc)
                is_day = sunrise_utc <= now_utc <= sunset_utc
            else:
                # fallback to timezone/local-hour heuristic
                local_hour = datetime.datetime.now().hour
                is_day = 7 <= local_hour <= 19
        except Exception as e:
            self._logger.debug(f"OpenWeather day/night check failed: {type(e).__name__}: {e}")
            local_hour = datetime.datetime.now().hour
            is_day = 7 <= local_hour <= 19

        if _ASTRAL_OK:
            try:
                if self._lat is None or self._lon is None:
                    raise ValueError("Coordinates not available for Astral computation")
                obs = Observer(latitude=float(self._lat), longitude=float(self._lon), elevation=0.0)
                if is_day:
                    az = float(sun_azimuth(obs, now_utc))
                    el = float(sun_elevation(obs, now_utc))
                    self._astro_text = f"Include the sun at its current position (azimuth {az:.0f}°, elevation {el:.0f}°)."
                else:
                    ph_days = float(moon_phase(now_utc))
                    ph_pct = max(0.0, min(100.0, (ph_days / 29.53) * 100.0))
                    self._astro_text = f"Include the moon with its current phase (~{ph_pct:.0f}%)."
                self._astro_text_timestamp = datetime.datetime.now()
                return self._astro_text
            except Exception as e:
                self._logger.debug(f"Astral calculation failed: {type(e).__name__}: {e}")
                local_time = self._get_local_time_str()
                self._astro_text = f"Include the current sun/moon position in the sky appropriate for ({local_time})."
                self._astro_text_timestamp = datetime.datetime.now()
                return self._astro_text
        else:
            local_time = self._get_local_time_str()
            self._astro_text = f"Include the current sun/moon position in the sky appropriate for ({local_time})."
            self._astro_text_timestamp = datetime.datetime.now()
            return self._astro_text

    def _get_local_time_str(self) -> str:
        """
        Return a human-friendly local time string determined from available timezone info.
        Priority:
         1. OpenWeather `timezone` offset (seconds) from cached weather raw payload
         2. `self._timezone_fallback` using `zoneinfo.ZoneInfo` if available
         3. System local time as fallback
        """
        try:
            # Ensure weather cache is present if possible
            if not self._weather_cache:
                try:
                    self._fetch_weather_data()
                except Exception:
                    pass

            raw = (self._weather_cache or {}).get("raw") or {}
            tz_offset = raw.get("timezone")
            if tz_offset is not None:
                try:
                    offs = int(tz_offset)
                    tz = datetime.timezone(datetime.timedelta(seconds=offs))
                    local_dt = datetime.datetime.now(datetime.timezone.utc).astimezone(tz)
                    res = local_dt.strftime("%Y-%m-%d %H:%M:%S %z")
                    try:
                        self._logger.debug("Local time computed using OpenWeather offset=%s: %s", tz_offset, res)
                    except Exception:
                        pass
                    return res
                except Exception:
                    pass

            if self._timezone_fallback and self._ZoneInfo:
                try:
                    tz = self._ZoneInfo(self._timezone_fallback)
                    local_dt = datetime.datetime.now(tz)
                    res = local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
                    try:
                        self._logger.debug("Local time computed using timezone fallback %s: %s", self._timezone_fallback, res)
                    except Exception:
                        pass
                    return res
                except Exception:
                    pass

            # Fallback to system local time
            local_dt = datetime.datetime.now()
            res = local_dt.strftime("%Y-%m-%d %H:%M:%S")
            try:
                self._logger.debug("Local time computed using system local time: %s", res)
            except Exception:
                pass
            return res
        except Exception:
            res = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                self._logger.debug("Local time computation failed; returning %s", res)
            except Exception:
                pass
            return res

    # --- NEW: determine day/night (reusing OpenWeather first, fallback to local time)
    def _is_daytime(self) -> bool:
        """
        Returns True if it's currently day at the configured coordinates, else False.
        Uses OpenWeather sunrise/sunset when possible; otherwise falls back to 07–19 local-hour heuristic.
        """
        # If coords invalid, prefer a configured timezone fallback when present
        if not self._coords_ok:
            try:
                if self._timezone_fallback and self._ZoneInfo:
                    now_local = datetime.datetime.now(self._ZoneInfo(self._timezone_fallback))
                    local_hour = now_local.hour
                else:
                    local_hour = datetime.datetime.now().hour
                return 7 <= local_hour <= 19
            except Exception:
                local_hour = datetime.datetime.now().hour
                return 7 <= local_hour <= 19
        # Fetch fresh weather if cache is missing or stale (important for fallback mode)
        if not self._is_weather_cache_valid():
            self._fetch_weather_data()

        try:
            sys_info = (self._weather_cache or {}).get("sys", {}) or {}
            sunrise_ts = sys_info.get("sunrise")
            sunset_ts = sys_info.get("sunset")
            if sunrise_ts and sunset_ts:
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                sunrise_utc = datetime.datetime.utcfromtimestamp(sunrise_ts).replace(tzinfo=datetime.timezone.utc)
                sunset_utc = datetime.datetime.utcfromtimestamp(sunset_ts).replace(tzinfo=datetime.timezone.utc)
                res = sunrise_utc <= now_utc <= sunset_utc
                try:
                    self._logger.debug(
                        "Daytime check (UTC): now=%s sunrise=%s sunset=%s -> %s",
                        now_utc, sunrise_utc, sunset_utc, res,
                    )
                except Exception:
                    pass
                return res
            # otherwise fall back to timezone/local heuristic below
        except Exception as e:
            self._logger.debug(f"Day/night check fallback: {type(e).__name__}: {e}")

        try:
            if self._timezone_fallback and self._ZoneInfo:
                now_local = datetime.datetime.now(self._ZoneInfo(self._timezone_fallback))
                local_hour = now_local.hour
            else:
                local_hour = datetime.datetime.now().hour
            return 7 <= local_hour <= 19
        except Exception:
            local_hour = datetime.datetime.now().hour
            return 7 <= local_hour <= 19

    # --- NEW: choose an appropriate fallback image path based on day/night
    def _choose_fallback_path(self) -> str:
        """
        Returns the best fallback image path in priority order:
        1) orientation-aware day/night path based on current time and orientation
        2) legacy single 'fallback_image_path'
        3) empty string if none available
        """
        try:
            is_day = self._is_daytime()
        except Exception as e:
            self._logger.debug(f"Failed to compute is_daytime: {type(e).__name__}: {e}")
            # Default to day for safety; path presence is validated below.
            is_day = True

        # Determine current orientation from the shared settings store
        orientation = self._get_current_orientation()

        # 1) Orientation-aware day/night specific paths
        candidate = ""
        if is_day:
            candidate = (
                self._fallback_image_path_day_portrait if orientation == "portrait" else self._fallback_image_path_day_landscape
            )
        else:
            candidate = (
                self._fallback_image_path_night_portrait if orientation == "portrait" else self._fallback_image_path_night_landscape
            )
        if candidate:
            try:
                self._logger.debug("Chosen orientation-aware fallback (day=%s, orientation=%s): %s", is_day, orientation, candidate)
            except Exception:
                pass
            return candidate

        # 2) Legacy single fallback
        try:
            self._logger.debug("Using legacy fallback image path: %s", self._fallback_image_path)
        except Exception:
            pass
        return self._fallback_image_path  # may be empty

    def get_fallback_path(self) -> str:
        """Public accessor for the time-relevant fallback image path.

        Returns an empty string when no fallback path is configured.
        """
        try:
            return self._choose_fallback_path()
        except Exception:
            return ""

    def _lighting_instructions(self) -> str:
        # If prepared context already set lighting text and still fresh, return it to avoid recomputation
        if self._lighting_text and self._lighting_text_timestamp:
            age = (datetime.datetime.now() - self._lighting_text_timestamp).total_seconds()
            if age < self._lighting_text_ttl_seconds:
                return self._lighting_text

        # Prefer lighting strings provided in config under the `lighting` key.
        astro = (self._sun_moon_context() or "").lower()
        # Detect twilight keywords first
        is_twilight = any(k in astro for k in ("civil twilight", "dusk", "dawn", "twilight"))
        if is_twilight:
            result = self._lighting_cfg.get("twilight") or (
                "Use twilight lighting: soft low-angle light, gentle shadows, a sky gradient, moderate contrast, and selective artificial lights beginning to appear."
            )
            self._lighting_text = result
            self._lighting_text_timestamp = datetime.datetime.now()
            return result

        # Use daytime detection for day/night; fall back to astro text if needed
        try:
            if self._is_daytime():
                result = self._lighting_cfg.get("day") or (
                    "Use daytime lighting: natural brightness, appropriate color temperature for the time, balanced contrast, and realistic shadows."
                )
                self._lighting_text = result
                self._lighting_text_timestamp = datetime.datetime.now()
                return result
            else:
                result = self._lighting_cfg.get("night") or (
                    "Render with low-light exposure: markedly darker scene, high contrast, cooler ambient tones, visible artificial lighting (street lamps, train interiors/headlights, illuminated windows), reduced sky luminance."
                )
                self._lighting_text = result
                self._lighting_text_timestamp = datetime.datetime.now()
                return result
        except Exception:
            # If day/night check fails, fall back to analyzing astro text
            if "night" in astro:
                result = self._lighting_cfg.get("night") or (
                    "Render with low-light exposure: markedly darker scene, high contrast, cooler ambient tones, visible artificial lighting (street lamps, train interiors/headlights, illuminated windows),reduced sky luminance."
                )
                self._lighting_text = result
                self._lighting_text_timestamp = datetime.datetime.now()
                return result
            result = self._lighting_cfg.get("day") or (
                "Use daytime lighting: natural brightness, appropriate color temperature for the time, balanced contrast, and realistic shadows."
            )
            self._lighting_text = result
            self._lighting_text_timestamp = datetime.datetime.now()
            return result

    def _choose_image_size(self) -> str:
        """
        Pick an appropriate image size string ("{w}x{h}") based on the selected model,
        configured model/image settings, and the display aspect ratio. Prefer non-square
        sizes when the model supports them and the display is non-square.
        """
        model = str(self._image_model)
        model_info = self._get_model_info(model)

        # Honor configured display orientation by swapping width/height when needed
        w = int(self._display_width)
        h = int(self._display_height)
        orient = (self._get_current_orientation() or self._display_orientation or "").lower()
        if orient.startswith("portrait") and w > h:
            w, h = h, w
        elif orient.startswith("landscape") and h > w:
            w, h = h, w

        def parse_size(s: str) -> Optional[Tuple[int, int]]:
            try:
                a, b = s.split("x")
                return int(a), int(b)
            except Exception:
                return None

        def cap_and_format(width: int, height: int) -> str:
            # Cap to max dimension while preserving aspect ratio
            maxd = max(1, int(self._max_image_dimension))
            if width <= maxd and height <= maxd:
                return f"{width}x{height}"
            # scale down so the larger side == maxd
            if width >= height:
                scale = maxd / float(width)
            else:
                scale = maxd / float(height)
            nw = max(1, int(round(width * scale)))
            nh = max(1, int(round(height * scale)))
            return f"{nw}x{nh}"

        asp = f"{w}x{h}"

        # Prefer orientation-honored size for OpenAI's GPT Image family when possible
        try:
            if model.startswith("gpt-image"):
                display_is_portrait = h > w
                # orientation-preferred candidates for gpt-image family
                if display_is_portrait:
                    pref_str = "1024x1536"
                elif w > h:
                    pref_str = "1536x1024"
                else:
                    pref_str = model_info.get("default", "1024x1024")

                # If model exposes allowed or preferred sizes, prefer the orientation match
                allowed = model_info.get("allowed_sizes") or []
                prefs = model_info.get("preferred_sizes") or []
                parsed = parse_size(pref_str)
                if parsed:
                    pw, ph = parsed
                    if pref_str in allowed or pref_str in prefs or not allowed:
                        return cap_and_format(pw, ph)
        except Exception:
            # If anything unexpected happens here, continue with normal selection logic
            pass
        # If the model exposes explicit allowed sizes (e.g., any gpt-image-*), choose among them.
        allowed = model_info.get("allowed_sizes") or []
        if allowed:
            # build parsed candidates
            candidates = []
            for s in allowed:
                parsed = parse_size(s)
                if not parsed:
                    continue
                pw, ph = parsed
                candidates.append((pw * ph, pw, ph, s))

            # display orientation
            display_is_portrait = h > w

            # matching candidates prefer those matching orientation (portrait/landscape)
            matching = []
            for area, pw, ph, s in candidates:
                if display_is_portrait and ph > pw:
                    matching.append((area, pw, ph, s))
                elif (not display_is_portrait) and pw > ph:
                    matching.append((area, pw, ph, s))
                elif pw == ph:
                    matching.append((area, pw, ph, s))

            use_set = matching or candidates

            # orientation strategy selection
            if self._orientation_strategy == "cover":
                # choose smallest area that covers display
                cover = [(area, pw, ph, s) for (area, pw, ph, s) in use_set if pw >= w and ph >= h]
                if cover:
                    cover.sort()
                    _, pw, ph, s = cover[0]
                    return cap_and_format(pw, ph)
                # otherwise choose largest available
                use_set.sort(reverse=True)
                _, pw, ph, s = use_set[0]
                return cap_and_format(pw, ph)
            else:
                # contain: choose largest that fits within display
                contain = [(area, pw, ph, s) for (area, pw, ph, s) in use_set if pw <= w and ph <= h]
                if contain:
                    contain.sort(reverse=True)
                    _, pw, ph, s = contain[0]
                    return cap_and_format(pw, ph)
                use_set.sort()
                _, pw, ph, s = use_set[0]
                return cap_and_format(pw, ph)

        # If model supports non-square and display isn't square, try exact match first
        if model_info.get("supports_non_square") and w != h:
            prefs = model_info.get("preferred_sizes", []) or []
            # orientation strategy: "cover" -> prefer sizes that cover the display; "contain" -> prefer sizes that fit within
            if self._orientation_strategy == "cover":
                # prefer preferred sizes that at least cover the display
                candidates = []
                for s in prefs:
                    parsed = parse_size(s)
                    if not parsed:
                        continue
                    pw, ph = parsed
                    if pw >= w and ph >= h:
                        candidates.append((pw * ph, pw, ph))
                if candidates:
                    # pick smallest area that still covers
                    candidates.sort()
                    _, pw, ph = candidates[0]
                    return cap_and_format(pw, ph)
                # no preferred covered; fall back to requested display size (capped)
                return cap_and_format(w, h)
            else:
                # contain: prefer preferred sizes that fit within the display (largest area)
                candidates = []
                for s in prefs:
                    parsed = parse_size(s)
                    if not parsed:
                        continue
                    pw, ph = parsed
                    if pw <= w and ph <= h:
                        candidates.append((pw * ph, pw, ph))
                if candidates:
                    candidates.sort(reverse=True)
                    _, pw, ph = candidates[0]
                    return cap_and_format(pw, ph)
                # else fall back to display size (capped)
                return cap_and_format(w, h)

        # Otherwise pick a square size: try preferred_sizes then default
        for s in model_info.get("preferred_sizes", []):
            if "x" in s:
                a, b = s.split("x")
                if a == b:
                    return s
        # last resort: use configured image.max_square_size or 1024
        max_sq = int((self._config.get("image", {}) or {}).get("max_square_size", 1024))
        return f"{max_sq}x{max_sq}"

    def _is_weather_cache_valid(self) -> bool:
        """
        Check if cached weather is still fresh (within TTL).
        Returns True if cache exists and is not stale, False otherwise.
        """
        if not self._weather_cache or not self._weather_cache_timestamp:
            return False
        age = (datetime.datetime.now() - self._weather_cache_timestamp).total_seconds()
        is_valid = age < self._weather_cache_ttl_seconds
        if not is_valid:
            try:
                self._logger.debug(
                    "Weather cache expired: age=%d seconds, TTL=%d seconds",
                    int(age),
                    self._weather_cache_ttl_seconds,
                )
            except Exception:
                pass
        return is_valid

    def _fetch_weather_data(self) -> Optional[dict]:
        """
        Fetch weather data once and cache it. Returns a dict with keys:
        city, weather_desc, sys (dict with sunrise/sunset timestamps), raw (full json)
        or None on failure.
        """
        try:
            if not self._coords_ok:
                return None
            base_url = "https://api.openweathermap.org/data/2.5/weather"
            api_key = (self._config.get("weather", {}) or {}).get("openweathermap_api_key")
            if not api_key:
                self._logger.debug("Missing weather.openweathermap_api_key in config; skipping weather fetch")
                return None
            url = f"{base_url}?lat={self._lat}&lon={self._lon}&units=metric&appid={api_key}"
            resp = requests.get(url, timeout=6.0)
            resp.raise_for_status()
            data = resp.json()
            city = data.get("name") or "the nearest major city"
            desc = (data.get("weather", [{}])[0].get("description") or "").strip().capitalize()
            sys_info = data.get("sys", {}) or {}
            self._weather_cache = {"city": city, "weather_desc": desc or "current local conditions", "sys": sys_info, "raw": data}
            self._weather_cache_timestamp = datetime.datetime.now()
            # Invalidate astro and lighting caches when weather is refreshed (so time/conditions update)
            self._astro_text = None
            self._astro_text_timestamp = None
            self._lighting_text = None
            self._lighting_text_timestamp = None
            # Log fetched weather details for debugging
            try:
                self._logger.info(
                    "Fetched weather: city=%s, desc=%s, sunrise=%s, sunset=%s",
                    city,
                    desc or "current local conditions",
                    sys_info.get("sunrise"),
                    sys_info.get("sunset"),
                )
            except Exception:
                pass
            return self._weather_cache
        except Exception as e:
            self._logger.debug(f"OpenWeather fetch failed: {type(e).__name__}: {e}")
            # Clear caches on fetch failure to avoid stale data
            self._weather_cache = None
            self._weather_cache_timestamp = None
            self._astro_text = None
            self._astro_text_timestamp = None
            self._lighting_text = None
            self._lighting_text_timestamp = None
            return None

    def _get_model_info(self, model: str) -> dict:
        """Return a dict describing model capabilities, overridable from config."""
        cfg_map = self._model_image_settings or {}
        cfg = cfg_map.get(model, {}) or {}

        # Normalize legacy/ambiguous model names: map `dall-e` -> `dall-e-2` (only DALL·E-2/3 supported)
        try:
            if model in ("dall-e", "dalle"):
                try:
                    self._logger.warning("Legacy model name '%s' detected; treating as 'dall-e-2'.", model)
                except Exception:
                    pass
                model = "dall-e-2"
        except Exception:
            pass

        # By default treat any `gpt-image-*` model as supporting non-square sizes.
        # DALL·E-2 and DALL·E-3 are treated as square-only by default (config can override).
        defaults = {
            "gpt-image-1": {
                "supports_non_square": True,
                "allowed_sizes": ["1024x1024", "1536x1024", "1024x1536"],
                "preferred_sizes": ["1536x1024", "1024x1536", "1024x1024"],
                "default": "1024x1024",
            },
            "dall-e-2": {"supports_non_square": False, "preferred_sizes": ["1024x1024"], "default": "1024x1024"},
            "dall-e-3": {"supports_non_square": False, "preferred_sizes": ["1024x1024"], "default": "1024x1024"},
        }

        model_info_source = defaults.get(model)
        if model_info_source is None and isinstance(model, str) and model.startswith("gpt-image-"):
            model_info_source = defaults.get("gpt-image-1")
        if model_info_source is None:
            model_info_source = {
                "supports_non_square": True,
                "preferred_sizes": ["1024x1024"],
                "default": "1024x1024",
            }
        model_info = model_info_source.copy()
        # apply overrides
        if "supports_non_square" in cfg:
            model_info["supports_non_square"] = bool(cfg.get("supports_non_square"))
        if "preferred_sizes" in cfg and isinstance(cfg.get("preferred_sizes"), list):
            model_info["preferred_sizes"] = cfg.get("preferred_sizes")
        if "default" in cfg:
            model_info["default"] = cfg.get("default")
        return model_info

    def _prepare_context(self) -> None:
        """Compute and cache all per-refresh decisions (weather, astro, lighting, model, size)."""
        # Refresh weather cache
        try:
            self._fetch_weather_data()
        except Exception:
            self._weather_cache = None

        city, weather_desc = self._fetch_city_and_weather_desc()

        # Astro text and lighting text (these functions will use cached weather)
        # Reset astro cache per refresh so azimuth/phase reflect current time
        self._astro_text = None
        try:
            self._astro_text = self._sun_moon_context()
        except Exception:
            self._astro_text = None
        try:
            self._lighting_text = self._lighting_instructions()
        except Exception:
            self._lighting_text = None

        # Model info + chosen image size
        try:
            self._model_info = self._get_model_info(str(self._image_model))
            self._image_size = self._choose_image_size()
        except Exception:
            self._model_info = None
            self._image_size = None

    def _coerce_openai_image_size(self, size: str) -> OpenAIImageSize:
        """Normalize dynamic size strings into the OpenAI SDK's accepted literal size values."""
        allowed_sizes = {
            "auto",
            "1024x1024",
            "1536x1024",
            "1024x1536",
            "256x256",
            "512x512",
            "1792x1024",
            "1024x1792",
        }
        if isinstance(size, str) and size in allowed_sizes:
            return cast(OpenAIImageSize, size)
        return "1024x1024"

        # Log prepared context (weather/time/OpenAI-related) for diagnostics
        try:
            self._logger.info(
                "Prepared context: city=%s, weather=%s, astro=%s, lighting=%s, model=%s, model_info=%s, image_size=%s",
                city,
                weather_desc,
                getattr(self, "_astro_text", None),
                getattr(self, "_lighting_text", None),
                getattr(self, "_image_model", None),
                getattr(self, "_model_info", None),
                getattr(self, "_image_size", None),
            )
        except Exception:
            pass

    # --- build the final prompt string
    def _build_dynamic_prompt(self) -> str:
        city, weather_desc = self._fetch_city_and_weather_desc()
        astro = self._sun_moon_context()
        coords_txt = f"{self._lat:.6f}, {self._lon:.6f}" if self._coords_ok else "unknown"
        style_txt = self._prompt_style or "80 anime style"
        lighting_txt = self._lighting_instructions()

        # Compose the prompt
        return (
            f"Generate an image in an {style_txt} style of location accurate {city} architecture with no signage. "
            f"Set the scene at the current time of day with accurate local weather: {weather_desc}. "
            f"{astro} {lighting_txt}"
            f" Incorporate the area's local train system and accurate city skyline into the composition and ensure that the major details are cropped within a centered 480px wide area."
        )

    def _download_image_to_file(self, image_url: str, output_path: str, timeout: float = 20.0) -> None:
        response = requests.get(image_url, timeout=timeout)
        response.raise_for_status()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(response.content)

    def _pixazo_image_dimensions(self) -> Tuple[int, int]:
        orientation = self._get_current_orientation()
        if orientation == "landscape":
            return 1024, 512
        return 512, 1024

    def _generate_pixazo_text_to_image(self, prompt: str, output_path: str) -> dict:
        if not self._pixazo_api_key:
            raise ValueError("Pixazo API key is not configured.")

        model = self._pixazo_model or "flux-schnell"
        if model != "flux-schnell":
            raise ValueError(f"Pixazo model '{model}' is not supported yet. Supported free model: flux-schnell.")

        width, height = self._pixazo_image_dimensions()
        request_url = "https://gateway.pixazo.ai/flux-1-schnell/v1/getData"
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "Ocp-Apim-Subscription-Key": self._pixazo_api_key,
        }
        payload = {
            "prompt": prompt,
            "num_steps": 4,
            "width": width,
            "height": height,
        }

        response = requests.post(request_url, headers=headers, json=payload, timeout=180)
        if response.status_code != 200:
            try:
                error_payload = response.json()
                error_message = error_payload.get("message") or error_payload.get("error") or str(error_payload)
            except Exception:
                error_message = response.text.strip() or f"Pixazo returned HTTP {response.status_code}"
            raise RuntimeError(f"Pixazo generation failed: {error_message}")

        data = response.json()
        output_url = data.get("output") if isinstance(data, dict) else None
        if not isinstance(output_url, str) or not output_url.strip():
            raise RuntimeError("Pixazo generation did not return an output URL.")

        self._download_image_to_file(output_url, output_path)
        return {
            "provider": "pixazo",
            "model": model,
            "size": f"{width}x{height}",
            "prompt": prompt,
            "path": output_path,
            "image_url": output_url,
        }

    def generate_test_image(self, output_path: str, prompt_override: Optional[str] = None) -> dict:
        if not self._coords_ok:
            raise ValueError("Invalid geo coordinates. Update the weather geo coordinates before generating a test image.")
        if self._provider == "pixazo":
            self._prepare_context()
            prompt = prompt_override or self._build_dynamic_prompt()
            return self._generate_pixazo_text_to_image(prompt, output_path)
        if not self._client:
            raise ValueError("OpenAI client is not initialized. Configure a valid OpenAI API key first.")

        self._prepare_context()
        prompt = prompt_override or self._build_dynamic_prompt()
        size = self._image_size or self._choose_image_size()
        size_arg = self._coerce_openai_image_size(size)

        result = self._client.images.generate(
            model=self._image_model,
            prompt=prompt,
            size=size_arg,
        )

        image_base64 = None
        image_url = None
        res_dict = None
        if isinstance(result, dict):
            res_dict = result
        else:
            try:
                res_dict = getattr(result, "__dict__", None) or None
            except Exception:
                res_dict = None

        data_list = None
        if res_dict and isinstance(res_dict.get("data", None), list):
            data_list = res_dict.get("data")
        else:
            try:
                data_attr = getattr(result, "data", None)
                if isinstance(data_attr, (list, tuple)):
                    data_list = list(data_attr)
            except Exception:
                data_list = None

        if data_list:
            first = data_list[0] if len(data_list) > 0 else None
            if isinstance(first, dict):
                image_base64 = first.get("b64_json") or first.get("b64") or first.get("base64") or first.get("image")
                image_url = first.get("url") or first.get("image_url")
            else:
                image_base64 = getattr(first, "b64_json", None) or getattr(first, "b64", None) or getattr(first, "base64", None)
                image_url = getattr(first, "url", None)

        if not image_base64 and res_dict:
            for key in ("b64_json", "b64", "base64", "image"):
                value = res_dict.get(key)
                if isinstance(value, str) and value:
                    image_base64 = value
                    break
            if not image_url:
                for key in ("url", "image_url", "image_url_https"):
                    value = res_dict.get(key)
                    if isinstance(value, str) and value:
                        image_url = value
                        break

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        if image_base64:
            output.write_bytes(base64.b64decode(image_base64))
        elif image_url:
            resp_img = requests.get(image_url, timeout=10.0)
            resp_img.raise_for_status()
            output.write_bytes(resp_img.content)
        else:
            raise RuntimeError("The image provider did not return an image payload.")

        return {
            "provider": self._provider,
            "model": self._image_model,
            "size": size,
            "prompt": prompt,
            "path": str(output),
        }

    def refresh_background_if_needed(self) -> None:
        """Generate & save a background image if TTL has expired (with robust fallback)."""
        try:
            if not self._should_refresh():
                return

            # Short-circuit if coordinates invalid
            if not self._coords_ok:
                self._logger.error(
                    "Coordinate parsing failed; applying fallback image and skipping generation."
                )
                self._apply_fallback_image()
                return

            if self._provider == "pixazo":
                if not self._pixazo_api_key:
                    self._logger.warning("Pixazo API key not configured; attempting fallback image.")
                    self._apply_fallback_image()
                    return
                self._prepare_context()
                prompt = self._build_dynamic_prompt()
                try:
                    result = self._generate_pixazo_text_to_image(prompt, self._outfile)
                    self._last_refresh = datetime.datetime.now()
                    self._logger.info(
                        "Pixazo background image refreshed -> %s (model=%s size=%s)",
                        self._outfile,
                        result.get("model"),
                        result.get("size"),
                    )
                    return
                except Exception as pixazo_err:
                    self._logger.error("Pixazo image generation failed: %s", pixazo_err, exc_info=True)
                    if not self._apply_fallback_image():
                        self._logger.warning("Fallback failed after Pixazo generation error; keeping previous background.")
                    return
            elif self._provider != "openai":
                self._logger.warning("AI image provider '%s' is not implemented in runtime generation; applying fallback image.", self._provider)
                self._apply_fallback_image()
                return

            # If there's no OpenAI client, fall back immediately
            if not self._client:
                self._logger.warning("OpenAI client not initialized; attempting fallback image.")
                self._apply_fallback_image()
                return

            # Prepare all per-refresh context (weather, astro, lighting, model info, image size)
            self._prepare_context()

            # Attempt to generate the image via Images API
            prompt = self._build_dynamic_prompt()
            # prefer the prepared image size if available
            size = self._image_size or self._choose_image_size()
            size_arg = self._coerce_openai_image_size(size)
            self._logger.debug("OpenAI image prompt: %s", prompt)
            self._logger.debug("Image generation model=%s size=%s", self._image_model, size)
            try:
                try:
                    self._logger.info(
                        "Generating image with model=%s size=%s city=%s weather=%s",
                        self._image_model,
                        size,
                        getattr(self, "_city", None),
                        getattr(self, "_weather_desc", None),
                    )
                except Exception:
                    pass

                # Make the request
                result = self._client.images.generate(
                    model=self._image_model,
                    prompt=prompt,
                    size=size_arg,
                )

                # Try to extract a base64 payload or a downloadable URL from common response shapes
                image_base64 = None
                image_url = None
                try:
                    # Normalize a dict-like view of the result when possible
                    res_dict = None
                    if isinstance(result, dict):
                        res_dict = result
                    else:
                        try:
                            res_dict = getattr(result, "__dict__", None) or None
                        except Exception:
                            res_dict = None

                    # Common shape: top-level `data` list
                    data_list = None
                    if res_dict and isinstance(res_dict.get("data", None), list):
                        data_list = res_dict.get("data")
                    else:
                        # Some SDKs expose `data` as an attribute
                        try:
                            data_attr = getattr(result, "data", None)
                            if isinstance(data_attr, (list, tuple)):
                                data_list = list(data_attr)
                        except Exception:
                            data_list = None

                    if data_list:
                        d0 = data_list[0] if len(data_list) > 0 else None
                        if isinstance(d0, dict):
                            image_base64 = d0.get("b64_json") or d0.get("b64") or d0.get("base64") or d0.get("image")
                            image_url = d0.get("url") or d0.get("image_url")
                        else:
                            image_base64 = getattr(d0, "b64_json", None) or getattr(d0, "b64", None) or getattr(d0, "base64", None)
                            image_url = getattr(d0, "url", None)

                    # Also check top-level fields for base64 or url
                    if not image_base64 and res_dict:
                        for k in ("b64_json", "b64", "base64", "image"):
                            v = res_dict.get(k)
                            if isinstance(v, str) and v:
                                image_base64 = v
                                break
                        if not image_url:
                            for k in ("url", "image_url", "image_url_https"):
                                v = res_dict.get(k)
                                if isinstance(v, str) and v:
                                    image_url = v
                                    break
                except Exception:
                    image_base64 = None
                    image_url = None

                # If no base64 payload found, but a URL was returned, try to download it.
                if not image_base64 and image_url:
                    try:
                        resp_img = requests.get(image_url, timeout=10.0)
                        resp_img.raise_for_status()
                        os.makedirs(os.path.dirname(self._outfile), exist_ok=True)
                        with open(self._outfile, "wb") as f:
                            f.write(resp_img.content)
                        self._last_refresh = datetime.datetime.now()
                        self._logger.info(f"AI background image fetched from URL -> {self._outfile} (source: {image_url})")
                        return
                    except Exception as e:
                        try:
                            self._logger.debug("Failed to download image from URL: %s", str(e))
                        except Exception:
                            pass

                # Log a compact summary of the response to help diagnose why no image was produced
                if not image_base64:
                    try:
                        # Build a safe, truncated summary of the result object
                        try:
                            if hasattr(result, "__dict__"):
                                res_summary = json.dumps({k: v for k, v in result.__dict__.items() if k != "data"}, default=str)
                            else:
                                res_summary = str(result)
                        except Exception:
                            res_summary = str(result)

                        # Attempt to extract explicit API error/moderation fields if present
                        try:
                            res_dict = None
                            if isinstance(result, dict):
                                res_dict = result
                            else:
                                try:
                                    res_dict = getattr(result, "__dict__", None) or None
                                except Exception:
                                    res_dict = None
                            if res_dict:
                                err = res_dict.get("error") or res_dict.get("errors")
                                if err:
                                    try:
                                        self._logger.error("Image API returned error field: %s", json.dumps(err, default=str))
                                    except Exception:
                                        self._logger.error("Image API returned error field: %s", str(err))
                                # moderation or status fields
                                mod = res_dict.get("moderation") or res_dict.get("policy") or res_dict.get("status")
                                if mod:
                                    try:
                                        self._logger.debug("Image API returned moderation/status info: %s", json.dumps(mod, default=str))
                                    except Exception:
                                        self._logger.debug("Image API returned moderation/status info: %s", str(mod))
                        except Exception:
                            pass

                        if len(res_summary) > 800:
                            res_summary = res_summary[:800] + "..."

                        self._logger.error(
                            "OpenAI returned no image payload. model=%s size=%s city=%s weather=%s prompt_len=%d model_info=%s image_size=%s result_summary=%s",
                            self._image_model,
                            size,
                            getattr(self, "_city", None),
                            getattr(self, "_weather_desc", None),
                            len(prompt) if prompt else 0,
                            getattr(self, "_model_info", None),
                            getattr(self, "_image_size", None),
                            res_summary,
                        )
                    except Exception:
                        # best-effort logging; continue to fallback
                        try:
                            self._logger.error("OpenAI returned no image payload and result could not be summarized.")
                        except Exception:
                            pass
                    # Also include the full raw result at debug level to aid diagnostics
                    try:
                        try:
                            self._logger.debug("Full image API result: %s", json.dumps(result, default=str))
                        except Exception:
                            self._logger.debug("Full image API result (str): %s", str(result))
                    except Exception:
                        pass

                    self._logger.warning(
                        "OpenAI returned no image payload; applying fallback image."
                    )
                    if not self._apply_fallback_image():
                        self._logger.warning("Fallback failed; keeping previous background.")
                    return
            except Exception as gen_err:
                # Log contextual info first (without secrets)
                try:
                    self._logger.error(
                        "Image generation failed: model=%s size=%s city=%s weather=%s prompt_len=%d model_info=%s image_size=%s",
                        self._image_model,
                        size,
                        getattr(self, "_city", None),
                        getattr(self, "_weather_desc", None),
                        len(prompt) if prompt else 0,
                        getattr(self, "_model_info", None),
                        getattr(self, "_image_size", None),
                    )
                except Exception:
                    pass

                # Exception details and truncated traceback
                try:
                    tb = traceback.format_exc()
                    if tb and len(tb) > 2000:
                        tb = tb[:2000] + "..."
                    self._logger.error("Image generation exception: %s", str(gen_err))
                    self._logger.debug("Image generation traceback (truncated): %s", tb)
                except Exception:
                    try:
                        self._logger.error(f"Image generation error: {gen_err}", exc_info=True)
                    except Exception:
                        pass

                # If the exception carries an HTTP/response body, try to log a truncated version
                try:
                    err_body = getattr(gen_err, "response", None) or getattr(gen_err, "body", None) or getattr(gen_err, "http_body", None)
                    if err_body:
                        s = str(err_body)
                        if len(s) > 1000:
                            s = s[:1000] + "..."
                        self._logger.debug("Image generation exception response/body (truncated): %s", s)
                except Exception:
                    pass

                if not self._apply_fallback_image():
                    self._logger.warning(
                        "Fallback failed after generation error; keeping previous background."
                    )
                return

            # Save generated image
            os.makedirs(os.path.dirname(self._outfile), exist_ok=True)
            with open(self._outfile, "wb") as f:
                f.write(base64.b64decode(image_base64))
            self._last_refresh = datetime.datetime.now()
            self._logger.info(f"AI background image refreshed -> {self._outfile}")
        except Exception as e:
            self._logger.error(f"Failed to refresh AI background image: {e}", exc_info=True)
            # On outer failure, still try fallback once
            self._apply_fallback_image()

    # --- simple fallback helper
    def _apply_fallback_image(self) -> bool:
        """
        Copies the configured fallback image to the output file.
        Returns True if successful, False otherwise.
        """
        try:
            fallback_path = self._choose_fallback_path()
            if not fallback_path:
                self._logger.warning(
                    "No fallback image path configured (day/night or default); cannot apply fallback."
                )
                return False
            if not os.path.isfile(fallback_path):
                self._logger.error(f"Fallback image not found: {fallback_path}")
                return False

            os.makedirs(os.path.dirname(self._outfile), exist_ok=True)
            shutil.copyfile(fallback_path, self._outfile)
            self._last_refresh = datetime.datetime.now()
            self._logger.info(
                f"Used fallback image -> {self._outfile} (source: {fallback_path})"
            )
            return True
        except Exception as fe:
            self._logger.error(f"Failed to apply fallback image: {fe}", exc_info=True)
            return False