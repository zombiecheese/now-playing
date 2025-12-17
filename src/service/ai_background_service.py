
import base64
import datetime
import logging
import os
import shutil
from typing import Optional, Tuple

import requests  # for city name & weather description when needed

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


class AIBackgroundService:

    def __init__(self) -> None:
        self._logger: logging.Logger = Logger().get_logger()
        self._config: dict = Config().get_config()

        # Output path (same as your DisplayService reads)
        self._outfile = (
            self._config.get("display", {}).get("weather_background_image")
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

        # OpenAI setup
        openai_cfg = self._config.get("openai", {}) or {}
        self._api_key = openai_cfg.get("api_key") or os.environ.get("OPENAI_API_KEY") or ""
        # NEW: support day/night-specific fallback images with legacy single-path as ultimate default
        self._fallback_image_path_day = openai_cfg.get("fallback_image_path_day") or ""
        self._fallback_image_path_night = openai_cfg.get("fallback_image_path_night") or ""
        self._fallback_image_path = openai_cfg.get("fallback_image_path") or ""

        self._client: Optional[OpenAI] = None
        self._last_refresh: Optional[datetime.datetime] = None

        if not self._api_key:
            self._logger.warning(
                "OpenAI API key missing. AI background generation will use fallback."
            )
        else:
            try:
                self._client = OpenAI(api_key=self._api_key)
            except Exception as e:
                self._logger.error(f"Failed to initialize OpenAI client: {e}")

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
        if not self._coords_ok:
            return "the nearest major city", "current local conditions"
        try:
            base_url = "https://api.openweathermap.org/data/2.5/weather"
            api_key = (self._config.get("weather", {}) or {}).get("openweathermap_api_key")
            if not api_key:
                raise RuntimeError("Missing weather.openweathermap_api_key in config")
            url = f"{base_url}?lat={self._lat}&lon={self._lon}&units=metric&appid={api_key}"
            resp = requests.get(url, timeout=6.0)
            resp.raise_for_status()
            data = resp.json()
            city = data.get("name") or "the nearest major city"
            # e.g., "overcast clouds" -> "Overcast clouds"
            desc = (data.get("weather", [{}])[0].get("description") or "").strip().capitalize()
            return city, desc or "current local conditions"
        except Exception as e:
            self._logger.debug(f"OpenWeather city/desc fetch failed: {type(e).__name__}: {e}")
            return "the nearest major city", "current local conditions"

    # --- helper to compute sun/moon context string
    def _sun_moon_context(self) -> str:
        """
        Returns a natural language snippet about sun or moon at current time & coords.
        Daytime -> sun azimuth/elevation; Night -> moon phase (as percentage).
        Fallbacks gracefully if astral isn't available or coords invalid.
        """
        if not self._coords_ok:
            return "Include the current sun/moon position in the sky appropriate for the local time."
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        # Decide day/night using OpenWeather sunrise/sunset when possible
        try:
            base_url = "https://api.openweathermap.org/data/2.5/weather"
            api_key = (self._config.get("weather", {}) or {}).get("openweathermap_api_key")
            if not api_key:
                raise RuntimeError("Missing weather.openweathermap_api_key in config")
            url = f"{base_url}?lat={self._lat}&lon={self._lon}&units=metric&appid={api_key}"
            resp = requests.get(url, timeout=6.0)
            resp.raise_for_status()
            data = resp.json()
            sys_info = data.get("sys", {}) or {}
            sunrise_utc = datetime.datetime.utcfromtimestamp(sys_info.get("sunrise", 0)).replace(
                tzinfo=datetime.timezone.utc
            )
            sunset_utc = datetime.datetime.utcfromtimestamp(sys_info.get("sunset", 0)).replace(
                tzinfo=datetime.timezone.utc
            )
            is_day = sunrise_utc <= now_utc <= sunset_utc
        except Exception as e:
            # if any failure, assume day between 07–19 local time
            self._logger.debug(f"OpenWeather day/night check failed: {type(e).__name__}: {e}")
            local_hour = datetime.datetime.now().hour
            is_day = 7 <= local_hour <= 19

        if _ASTRAL_OK:
            try:
                # Astral v2+ expects Observer + aware datetime (UTC is fine)
                obs = Observer(latitude=float(self._lat), longitude=float(self._lon), elevation=0.0)
                if is_day:
                    az = float(sun_azimuth(obs, now_utc))   # aware UTC datetime
                    el = float(sun_elevation(obs, now_utc)) # aware UTC datetime
                    return f"Include the sun at its current position (azimuth {az:.0f}°, elevation {el:.0f}°)."
                else:
                    # Astral moon.phase returns phase-day in [0..~29.53], not a percent
                    ph_days = float(moon_phase(now_utc))
                    ph_pct = max(0.0, min(100.0, (ph_days / 29.53) * 100.0))
                    return f"Include the moon with its current phase (~{ph_pct:.0f}%)."
            except Exception as e:
                self._logger.debug(f"Astral calculation failed: {type(e).__name__}: {e}")
                return "Include the current sun/moon position in the sky appropriate for the local time."
        else:
            # Astral not available
            return "Include the current sun/moon position in the sky appropriate for the local time."

    # --- NEW: determine day/night (reusing OpenWeather first, fallback to local time)
    def _is_daytime(self) -> bool:
        """
        Returns True if it's currently day at the configured coordinates, else False.
        Uses OpenWeather sunrise/sunset when possible; otherwise falls back to 07–19 local-hour heuristic.
        """
        if not self._coords_ok:
            # If coords invalid, use the same heuristic as in _sun_moon_context()
            local_hour = datetime.datetime.now().hour
            return 7 <= local_hour <= 19
        try:
            base_url = "https://api.openweathermap.org/data/2.5/weather"
            api_key = (self._config.get("weather", {}) or {}).get("openweathermap_api_key")
            if not api_key:
                raise RuntimeError("Missing weather.openweathermap_api_key in config")
            url = f"{base_url}?lat={self._lat}&lon={self._lon}&units=metric&appid={api_key}"
            resp = requests.get(url, timeout=6.0)
            resp.raise_for_status()
            data = resp.json()
            sys_info = data.get("sys", {}) or {}
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            sunrise_utc = datetime.datetime.utcfromtimestamp(sys_info.get("sunrise", 0)).replace(
                tzinfo=datetime.timezone.utc
            )
            sunset_utc = datetime.datetime.utcfromtimestamp(sys_info.get("sunset", 0)).replace(
                tzinfo=datetime.timezone.utc
            )
            return sunrise_utc <= now_utc <= sunset_utc
        except Exception as e:
            self._logger.debug(f"Day/night check fallback: {type(e).__name__}: {e}")
            local_hour = datetime.datetime.now().hour
            return 7 <= local_hour <= 19

    # --- NEW: choose an appropriate fallback image path based on day/night
    def _choose_fallback_path(self) -> str:
        """
        Returns the best fallback image path in priority order:
        1) day/night-specific path based on current time
        2) legacy single 'fallback_image_path'
        3) empty string if none available
        """
        try:
            is_day = self._is_daytime()
        except Exception as e:
            self._logger.debug(f"Failed to compute is_daytime: {type(e).__name__}: {e}")
            # Default to day for safety; path presence is validated below.
            is_day = True

        # Prefer specific paths when present
        candidate = self._fallback_image_path_day if is_day else self._fallback_image_path_night
        if candidate:
            return candidate
        return self._fallback_image_path  # may be empty

    def _lighting_instructions(self) -> str:
        astro = (self._sun_moon_context() or "").lower()
        if "astronomical night" in astro or "nautical night" in astro or "night" in astro:
            return (
                "Render with low-light exposure: markedly darker scene, high contrast, cooler ambient tones, "
                "visible artificial lighting (street lamps, train interiors/headlights, illuminated windows), "
                "specular highlights on wet surfaces, reduced sky luminance."
            )
        if "civil twilight" in astro or "dusk" in astro or "dawn" in astro:
            return (
                "Use twilight lighting: soft low-angle light, gentle shadows, sky gradient, moderate contrast, "
                "selective artificial lights beginning to appear."
            )
        return (
            "Use daytime lighting: natural brightness, appropriate color temperature for the time, "
            "balanced contrast, and realistic shadows."
        )

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

            # If there's no OpenAI client, fall back immediately
            if not self._client:
                self._logger.warning("OpenAI client not initialized; attempting fallback image.")
                self._apply_fallback_image()
                return

            # Attempt to generate the image via Images API
            prompt = self._build_dynamic_prompt()
            self._logger.debug("OpenAI image prompt: %s", prompt)
            try:
                result = self._client.images.generate(
                    model="gpt-image-1",  # Images model
                    prompt=prompt,
                    size="1024x1024",     # adjust to your display pipeline (e.g., 800x480 then fit)
                )
                image_base64 = getattr(result.data[0], "b64_json", None)
                if not image_base64:
                    self._logger.warning(
                        "OpenAI returned no image payload; applying fallback image."
                    )
                    if not self._apply_fallback_image():
                        self._logger.warning("Fallback failed; keeping previous background.")
                    return
            except Exception as gen_err:
                self._logger.error(f"Image generation error: {gen_err}", exc_info=True)
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