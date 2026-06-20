
import logging
import time
import traceback
import os
from io import BytesIO
from typing import Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

from service.weather_service import WeatherInfo
from service.song_identify_service import SongInfo

from inky.auto import auto
from inky.inky_uc8159 import CLEAN


class DisplayService:
    """
    Handles composition and rendering of images on the Inky Impression display,
    for both 'playing' (album art + text) and screensaver/weather modes.
    """

    def __init__(self) -> None:
        from logger import Logger
        from config import Config

        # Config / logging
        self._config: dict = Config().get_config()
        self._logger: logging.Logger = Logger().get_logger()
        self._project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

        # Inky hardware
        self._inky = auto()

        # HTTP session for image fetches (reuses connections)
        self._http = requests.Session()

        # Orientation & rotation (defaults; runtime overrides come from toggle_state)
        dcfg = self._config.get("display", {})
        self._orientation = "landscape"
        self._portrait_rotate_degrees = 90
        self._landscape_rotate_degrees = 0

        # Configurable AI dot margins (read orientation from toggle_state at runtime)
        self._ai_dot_margin_x_px = int(dcfg.get("ai_dot_margin_x_px", 20))
        self._ai_dot_margin_y_px = int(dcfg.get("ai_dot_margin_y_px", 20))

        # Weather background (file path optional)
        self._weather_bg_path = dcfg.get("weather_background_image") or dcfg.get("screensaver_image")

        # Persist the final rendered frame (with overlays) for web preview.
        web_cfg = self._config.get("web_interface", {}) if isinstance(self._config.get("web_interface", {}), dict) else {}
        preview_default = os.path.join(os.path.dirname(__file__), "..", "..", "config", "current_display_preview.png")
        preview_cfg = web_cfg.get("preview_image_path")
        self._preview_image_path = os.path.abspath(preview_cfg) if preview_cfg else os.path.abspath(preview_default)

        # Text layout options
        self._text_alignment_portrait = (dcfg.get("text_alignment_portrait") or "left").lower()
        self._text_alignment_landscape = (dcfg.get("text_alignment_landscape") or "left").lower()
        self._wrap_break_long_words = bool(dcfg.get("text_wrap_break_long_words", True))
        self._wrap_hyphenate = bool(dcfg.get("text_wrap_hyphenate", False))
        self._line_spacing_px = int(dcfg.get("text_line_spacing_px", 0))

        # Backdrop tuning
        self._backdrop_blur_radius = int(dcfg.get("backdrop_blur_radius", 12))
        self._backdrop_darken_alpha = int(dcfg.get("backdrop_darken_alpha", 120))
        self._backdrop_use_gradient = bool(dcfg.get("backdrop_use_gradient", False))

        # Album art sizing / offsets
        self._album_cover_px = int(dcfg.get("small_album_cover_px", 250))

        # Text offsets per-orientation (remove global/shared offsets; default to 0)
        self._text_offset_left_px_portrait = int(dcfg.get("text_offset_left_px_portrait", 0))
        self._text_offset_right_px_portrait = int(dcfg.get("text_offset_right_px_portrait", 0))
        self._text_offset_top_px_portrait = int(dcfg.get("text_offset_top_px_portrait", 0))
        self._text_offset_bottom_px_portrait = int(dcfg.get("text_offset_bottom_px_portrait", 0))
        self._text_offset_text_shadow_px_portrait = int(dcfg.get("text_offset_text_shadow_px_portrait", 0))

        self._text_offset_left_px_landscape = int(dcfg.get("text_offset_left_px_landscape", 0))
        self._text_offset_right_px_landscape = int(dcfg.get("text_offset_right_px_landscape", 0))
        self._text_offset_top_px_landscape = int(dcfg.get("text_offset_top_px_landscape", 0))
        self._text_offset_bottom_px_landscape = int(dcfg.get("text_offset_bottom_px_landscape", 0))
        self._text_offset_text_shadow_px_landscape = int(dcfg.get("text_offset_text_shadow_px_landscape", 0))

        # Album-specific offsets per-orientation (separate from text offsets). Default to 0
        self._album_offset_left_px_portrait = int(dcfg.get("album_offset_left_px_portrait", 0))
        self._album_offset_top_px_portrait = int(dcfg.get("album_offset_top_px_portrait", 0))
        self._album_offset_right_px_portrait = int(dcfg.get("album_offset_right_px_portrait", 0))
        self._album_offset_bottom_px_portrait = int(dcfg.get("album_offset_bottom_px_portrait", 0))

        self._album_offset_left_px_landscape = int(dcfg.get("album_offset_left_px_landscape", 0))
        self._album_offset_top_px_landscape = int(dcfg.get("album_offset_top_px_landscape", 0))
        self._album_offset_right_px_landscape = int(dcfg.get("album_offset_right_px_landscape", 0))
        self._album_offset_bottom_px_landscape = int(dcfg.get("album_offset_bottom_px_landscape", 0))

        # Fonts (cached)
        self._font_title: ImageFont.FreeTypeFont
        self._font_subtitle: ImageFont.FreeTypeFont
        # CJK fallback fonts: store all TTC variants {language: (title_font, subtitle_font)}
        self._font_fallback_variants: dict[str, Tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]] = {}
        self._load_fonts_from_display_config(dcfg)

        # Font metrics cache: {font_id: {"height": int, "ascent": int, "descent": int}}
        self._font_metrics_cache: dict[int, dict[str, int]] = {}

    def _resolve_project_relative_path(self, configured_path: Optional[str]) -> Optional[str]:
        path = (configured_path or "").strip()
        if not path:
            return None
        if os.path.isabs(path):
            return path
        return os.path.abspath(os.path.join(self._project_root, path))

    @staticmethod
    def _safe_int(value: object, default_value: int, minimum: int = 1) -> int:
        try:
            parsed = int(value)
        except Exception:
            return default_value
        return max(minimum, parsed)

    def _load_fonts_from_display_config(self, dcfg: dict) -> None:
        configured_font_path = dcfg.get("font_path")
        configured_fallback_path = dcfg.get("font_fallback_path")
        fsize_title = self._safe_int(dcfg.get("font_size_title", 48), 48)
        fsize_subtitle = self._safe_int(dcfg.get("font_size_subtitle", 32), 32)

        resolved_font_path = self._resolve_project_relative_path(configured_font_path)
        resolved_fallback_path = self._resolve_project_relative_path(configured_fallback_path)

        try:
            if not resolved_font_path:
                raise ValueError("Font path missing in config.display.font_path")
            self._font_title = ImageFont.truetype(resolved_font_path, fsize_title)
            self._font_subtitle = ImageFont.truetype(resolved_font_path, fsize_subtitle)
            self._logger.info(
                "Loaded display fonts: path=%s title_size=%s subtitle_size=%s",
                resolved_font_path,
                fsize_title,
                fsize_subtitle,
            )
        except Exception as e:
            # Fallback to default bitmap font if truetype fails
            self._logger.warning(
                "Falling back to default font: %s (configured font_path=%s)",
                e,
                configured_font_path,
            )
            self._font_title = ImageFont.load_default()
            self._font_subtitle = ImageFont.load_default()

        self._font_fallback_variants = {}
        if resolved_fallback_path:
            # TTC indices: 0=Japanese, 1=Korean, 2=Simplified Chinese, 3=Traditional Chinese
            ttc_variants = [("ja", 0), ("ko", 1), ("zh-Hans", 2), ("zh-Hant", 3)]
            loaded_count = 0
            for lang_code, index in ttc_variants:
                try:
                    title_font = ImageFont.truetype(resolved_fallback_path, fsize_title, index=index)
                    subtitle_font = ImageFont.truetype(resolved_fallback_path, fsize_subtitle, index=index)
                    self._font_fallback_variants[lang_code] = (title_font, subtitle_font)
                    loaded_count += 1
                except Exception as e:
                    self._logger.debug(f"Could not load TTC index {index} for {lang_code}: {e}")

            if loaded_count > 0:
                self._logger.info(
                    "CJK fallback fonts loaded: %s variants from %s",
                    loaded_count,
                    resolved_fallback_path,
                )
            else:
                self._logger.warning(
                    "No CJK fallback fonts could be loaded from: %s",
                    resolved_fallback_path,
                )
        else:
            self._logger.warning("No fallback font path configured (config.display.font_fallback_path)")

        # Font objects changed; invalidate metrics cache so subsequent layout uses updated sizes.
        self._font_metrics_cache = {}

    def reload_display_settings(self) -> None:
        from config import Config

        self._config = Config().get_config()
        dcfg = self._config.get("display", {}) if isinstance(self._config.get("display", {}), dict) else {}

        self._weather_bg_path = dcfg.get("weather_background_image") or dcfg.get("screensaver_image")
        self._text_alignment_portrait = (dcfg.get("text_alignment_portrait") or "left").lower()
        self._text_alignment_landscape = (dcfg.get("text_alignment_landscape") or "left").lower()
        self._wrap_break_long_words = bool(dcfg.get("text_wrap_break_long_words", True))
        self._wrap_hyphenate = bool(dcfg.get("text_wrap_hyphenate", False))
        self._line_spacing_px = int(dcfg.get("text_line_spacing_px", 0))
        self._backdrop_blur_radius = int(dcfg.get("backdrop_blur_radius", 12))
        self._backdrop_darken_alpha = int(dcfg.get("backdrop_darken_alpha", 120))
        self._backdrop_use_gradient = bool(dcfg.get("backdrop_use_gradient", False))
        self._album_cover_px = int(dcfg.get("small_album_cover_px", 250))

        self._text_offset_left_px_portrait = int(dcfg.get("text_offset_left_px_portrait", 0))
        self._text_offset_right_px_portrait = int(dcfg.get("text_offset_right_px_portrait", 0))
        self._text_offset_top_px_portrait = int(dcfg.get("text_offset_top_px_portrait", 0))
        self._text_offset_bottom_px_portrait = int(dcfg.get("text_offset_bottom_px_portrait", 0))
        self._text_offset_text_shadow_px_portrait = int(dcfg.get("text_offset_text_shadow_px_portrait", 0))

        self._text_offset_left_px_landscape = int(dcfg.get("text_offset_left_px_landscape", 0))
        self._text_offset_right_px_landscape = int(dcfg.get("text_offset_right_px_landscape", 0))
        self._text_offset_top_px_landscape = int(dcfg.get("text_offset_top_px_landscape", 0))
        self._text_offset_bottom_px_landscape = int(dcfg.get("text_offset_bottom_px_landscape", 0))
        self._text_offset_text_shadow_px_landscape = int(dcfg.get("text_offset_text_shadow_px_landscape", 0))

        self._album_offset_left_px_portrait = int(dcfg.get("album_offset_left_px_portrait", 0))
        self._album_offset_top_px_portrait = int(dcfg.get("album_offset_top_px_portrait", 0))
        self._album_offset_right_px_portrait = int(dcfg.get("album_offset_right_px_portrait", 0))
        self._album_offset_bottom_px_portrait = int(dcfg.get("album_offset_bottom_px_portrait", 0))

        self._album_offset_left_px_landscape = int(dcfg.get("album_offset_left_px_landscape", 0))
        self._album_offset_top_px_landscape = int(dcfg.get("album_offset_top_px_landscape", 0))
        self._album_offset_right_px_landscape = int(dcfg.get("album_offset_right_px_landscape", 0))
        self._album_offset_bottom_px_landscape = int(dcfg.get("album_offset_bottom_px_landscape", 0))

        self._ai_dot_margin_x_px = int(dcfg.get("ai_dot_margin_x_px", 20))
        self._ai_dot_margin_y_px = int(dcfg.get("ai_dot_margin_y_px", 20))

        self._load_fonts_from_display_config(dcfg)

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def clean_display(self) -> None:
        """
        Fast, safe "clean" routine. Avoids per-pixel loops and uses two white
        frames to reduce ghosting. If you need UC8159 CLEAN specifically,
        consider building a palette-based image rather than calling set_pixel.
        """
        try:
            w, h = self._inky.width, self._inky.height
            white = Image.new("RGB", (w, h), color=(255, 255, 255))
            for _ in range(2):
                self._inky.set_image(white, saturation=1.0)
                self._inky.show()
                time.sleep(0.5)
        except Exception as e:
            self._logger.error(f"Error cleaning display: {e}")
            self._logger.error(traceback.format_exc())

    def set_orientation(
        self,
        orientation: str,
        portrait_rotate_degrees: Optional[int] = None,
        landscape_rotate_degrees: Optional[int] = None,
    ) -> None:
        """
        Dynamically change the display orientation and (optionally) rotation degrees.
        Valid values for orientation: "portrait" or "landscape".
        """
        orientation = (orientation or "landscape").lower()
        if orientation not in ("portrait", "landscape"):
            self._logger.warning(
                f"Invalid orientation '{orientation}'; keeping current '{self._orientation}'"
            )
            return

        if portrait_rotate_degrees is not None:
            self._portrait_rotate_degrees = int(portrait_rotate_degrees)
        if landscape_rotate_degrees is not None:
            self._landscape_rotate_degrees = int(landscape_rotate_degrees)

        self._orientation = orientation
        self._logger.info(
            "Orientation changed to %s (portrait_rotate=%s, landscape_rotate=%s)",
            self._orientation,
            self._portrait_rotate_degrees,
            self._landscape_rotate_degrees,
        )

    def update_display_to_playing(self, song_info: SongInfo) -> None:
        """
        Render 'playing' screen: backdrop (artist or blurred album), album cover,
        title (song), subtitle (artist), and meta (album + year).
        """
        # Album art with fallback to black background
        album_cover_image: Optional[Image.Image] = self._fetch_image(getattr(song_info, "album_art", None))

        # Final fallback to black background
        if not album_cover_image:
            album_cover_image = self._make_fallback_background().convert("RGBA")
            self._logger.info("No album art found, using black background")

        # Build meta line (album (year) | album | year)
        album_meta = ""
        if (song_info.album or "") or (song_info.release_year or ""):
            if (song_info.album or "") and (song_info.release_year or ""):
                album_meta = f"{song_info.album} ({song_info.release_year})"
            else:
                album_meta = (song_info.album or "") or (song_info.release_year or "")

        display_image = self._generate_display_image(
            base_image=album_cover_image,
            title=song_info.title or "",
            subtitle=song_info.artist or "",
            mode="playing",
            meta=album_meta,
        )
        self._show_image_on_display(display_image, show_ai_dot=False)



    def update_display_to_screensaver(self, weather_info: WeatherInfo, show_ai_dot: bool = False, fallback_image_path: Optional[str] = None) -> None:
        """
        Render screensaver/weather screen.
        """
        # Background image (file) with fallback
        chosen_path = fallback_image_path or self._weather_bg_path
        if chosen_path:
            try:
                # Align local file loading behavior with fetched images so EXIF
                # orientation/mirroring tags are honored before rendering.
                bg_image = ImageOps.exif_transpose(Image.open(chosen_path)).convert("RGBA")
            except Exception as e:
                self._logger.error(
                    f"Failed to load weather background '{chosen_path}': {e}"
                )
                bg_image = self._make_fallback_background().convert("RGBA")
        else:
            bg_image = self._make_fallback_background().convert("RGBA")

        # Safe text extraction with sane defaults so we never raise on startup
        temp = self._safe_text(getattr(weather_info, "temperature", None))
        raw_sub = self._safe_text(getattr(weather_info, "sub_description", None))
        parts = [p.strip() for p in raw_sub.split(".") if p.strip()]

        parsed_feels = ""
        parsed_desc = raw_sub
        if parts and parts[0].lower().startswith("feels like"):
            parsed_feels = parts[0]              # e.g., "Feels like 13 C"
            parsed_desc = ". ".join(parts[1:])  # e.g., "Overcast Clouds"

        if parsed_desc:
            parsed_desc = parsed_desc.rstrip(".") + "."

        # Decide what goes on each line
        title = temp
        subtitle = parsed_desc
        meta = parsed_feels

        display_image = self._generate_display_image(
            base_image=bg_image,
            title=title,
            subtitle=subtitle,
            mode="weather",
            meta=meta,
        )
        self._show_image_on_display(display_image, show_ai_dot=show_ai_dot)


    # ---------------------------------------------------------------------
    # Sizing & orientation helpers
    # ---------------------------------------------------------------------

    def _hardware_size(self) -> Tuple[int, int]:
        return (self._inky.width, self._inky.height)

    def _canvas_size(self) -> Tuple[int, int]:
        hw_w, hw_h = self._hardware_size()
        if self._orientation == "portrait":
            # Swap canvas so composition matches intended orientation
            return (hw_h, hw_w)
        return (hw_w, hw_h)

    def _orient_for_hardware(self, image: Image.Image) -> Image.Image:
        """Rotate canvas for portrait if configured."""
        if self._orientation == "portrait":
            return image.rotate(self._portrait_rotate_degrees, expand=True)
        if self._landscape_rotate_degrees:
            return image.rotate(self._landscape_rotate_degrees, expand=True)
        return image

    def _finalize_for_hardware(self, image: Image.Image) -> Image.Image:
        """
        Ensure final size and mode match the Inky hardware expectations.
        """
        return ImageOps.fit(
            image.convert("RGB"),
            (self._inky.width, self._inky.height),
            method=Image.LANCZOS,
            centering=(0.5, 0.5),
        )

    # ---------------------------------------------------------------------
    # Offset helpers (per-orientation)
    # ---------------------------------------------------------------------

    def _get_text_offset_left_px(self) -> int:
        return (
            self._text_offset_left_px_portrait
            if self._orientation == "portrait"
            else self._text_offset_left_px_landscape
        )

    def _get_text_offset_right_px(self) -> int:
        return (
            self._text_offset_right_px_portrait
            if self._orientation == "portrait"
            else self._text_offset_right_px_landscape
        )

    def _get_text_offset_top_px(self) -> int:
        return (
            self._text_offset_top_px_portrait
            if self._orientation == "portrait"
            else self._text_offset_top_px_landscape
        )

    def _get_text_offset_bottom_px(self) -> int:
        return (
            self._text_offset_bottom_px_portrait
            if self._orientation == "portrait"
            else self._text_offset_bottom_px_landscape
        )

    def _get_text_shadow_px(self) -> int:
        return (
            self._text_offset_text_shadow_px_portrait
            if self._orientation == "portrait"
            else self._text_offset_text_shadow_px_landscape
        )

    def _get_album_offset_left_px(self) -> int:
        return (
            self._album_offset_left_px_portrait
            if self._orientation == "portrait"
            else self._album_offset_left_px_landscape
        )

    def _get_album_offset_right_px(self) -> int:
        return (
            self._album_offset_right_px_portrait
            if self._orientation == "portrait"
            else self._album_offset_right_px_landscape
        )

    def _get_album_offset_top_px(self) -> int:
        return (
            self._album_offset_top_px_portrait
            if self._orientation == "portrait"
            else self._album_offset_top_px_landscape
        )

    def _get_album_offset_bottom_px(self) -> int:
        return (
            self._album_offset_bottom_px_portrait
            if self._orientation == "portrait"
            else self._album_offset_bottom_px_landscape
        )

    # ---------------------------------------------------------------------
    # Composition
    # ---------------------------------------------------------------------

    def _generate_display_image(
        self,
        base_image: Image.Image,
        title: str,
        subtitle: str,
        mode: str,
        meta: str = "",
        artist_backdrop: Optional[Image.Image] = None,
    ) -> Image.Image:
        canvas_w, canvas_h = self._canvas_size()

        if mode == "weather":
            composed = self._fit_background_image(base_image, canvas_w, canvas_h)
            self._add_text(composed, title, subtitle, canvas_w, canvas_h, meta)

        elif mode == "playing":
            if artist_backdrop:
                composed = self._compose_playing_with_backdrop(
                    album_img=base_image,
                    backdrop_img=artist_backdrop,
                    canvas_w=canvas_w,
                    canvas_h=canvas_h,
                    already_prepared=False,
                )
            else:
                # Blur/darken album art as backdrop
                backdrop = self._prepare_backdrop(base_image, canvas_w, canvas_h)
                composed = self._compose_playing_with_backdrop(
                    album_img=base_image,
                    backdrop_img=backdrop,
                    canvas_w=canvas_w,
                    canvas_h=canvas_h,
                    already_prepared=True,
                )
            self._add_text(composed, title, subtitle, canvas_w, canvas_h, meta)

        else:
            # Default fallback mode: fit background + add text
            composed = self._fit_background_image(base_image, canvas_w, canvas_h)
            self._add_text(composed, title, subtitle, canvas_w, canvas_h, meta)

        final_img = self._orient_for_hardware(composed)
        return final_img

    def _fit_background_image(
        self, image: Image.Image, target_w: int, target_h: int
    ) -> Image.Image:
        return ImageOps.fit(
            image.convert("RGBA"),
            (target_w, target_h),
            method=Image.LANCZOS,
            centering=(0.5, 0.5),
        )

    def _compose_playing_with_backdrop(
        self,
        album_img: Image.Image,
        backdrop_img: Image.Image,
        canvas_w: int,
        canvas_h: int,
        already_prepared: bool = False,
    ) -> Image.Image:
        """
        Build a backdrop (blur + darken), then paste album art fully opaque.
        Portrait: album centered above text; Landscape: album at left/top offsets.
        """
        # 1) Prepare RGBA backdrop to canvas
        frame = (
            backdrop_img.copy().convert("RGBA")
            if already_prepared
            else self._prepare_backdrop(backdrop_img, canvas_w, canvas_h)
        )

        # 2) Paste album art (square fit)
        cover = self._cover_image(album_img, self._album_cover_px)

        if self._orientation == "portrait":
            # Use album-specific bottom offset when reserving space for text
            reserved_bottom = (
                self._get_album_offset_bottom_px()
                + self._font_title.size
                + self._font_subtitle.size
                + max(2, self._get_text_shadow_px())
                + max(0, self._line_spacing_px)
            )
            usable_h = max(0, canvas_h - reserved_bottom)
            square_size = min(min(canvas_w, usable_h), self._album_cover_px)
            cover = ImageOps.fit(
                album_img.convert("RGBA"),
                (square_size, square_size),
                method=Image.LANCZOS,
                centering=(0.5, 0.5),
            )

            # Vertical placement: prefer configured album top offset but clamp
            y_top = min(
                self._get_album_offset_top_px(),
                max(0, (canvas_h - reserved_bottom - square_size)),
            )

            # Horizontal placement: center by default, then apply any album left/right offsets
            center_x = (canvas_w - square_size) // 2
            x_left = center_x + (self._get_album_offset_left_px() - self._get_album_offset_right_px())
            # Clamp to visible canvas
            x_left = max(0, min(x_left, max(0, canvas_w - square_size)))

            frame.paste(cover, (x_left, y_top), cover)
        else:
            # Landscape: simple left/top offsets; text goes at the bottom via _add_text()
            x = self._get_album_offset_left_px()
            y = self._get_album_offset_top_px()
            frame.paste(cover, (x, y), cover)

        return frame

    def _prepare_backdrop(
        self, src_img: Image.Image, canvas_w: int, canvas_h: int
    ) -> Image.Image:
        """
        Resize to canvas, blur, and darken for text readability.
        """
        bg = self._fit_background_image(src_img, canvas_w, canvas_h)  # RGBA

        # Blur (improves legibility under text/icons)
        if self._backdrop_blur_radius > 0:
            bg = bg.filter(ImageFilter.GaussianBlur(radius=self._backdrop_blur_radius))

        # Darken overlay (constant or gradient)
        if self._backdrop_use_gradient:
            overlay = self._vertical_gradient(
                (canvas_w, canvas_h),
                (0, 0, 0, 40),
                (0, 0, 0, self._backdrop_darken_alpha),
            )
        else:
            overlay = Image.new(
                "RGBA", (canvas_w, canvas_h), (0, 0, 0, self._backdrop_darken_alpha)
            )
        bg = Image.alpha_composite(bg, overlay)
        return bg

    def _vertical_gradient(
        self,
        size: Tuple[int, int],
        top_rgba: Tuple[int, int, int, int],
        bottom_rgba: Tuple[int, int, int, int],
    ) -> Image.Image:
        """Create a vertical RGBA gradient overlay (top -> bottom)."""
        w, h = size
        grad = Image.new("RGBA", (w, h))
        draw = ImageDraw.Draw(grad)
        for y in range(h):
            t = y / float(h - 1) if h > 1 else 0.0
            r = int(top_rgba[0] * (1 - t) + bottom_rgba[0] * t)
            g = int(top_rgba[1] * (1 - t) + bottom_rgba[1] * t)
            b = int(top_rgba[2] * (1 - t) + bottom_rgba[2] * t)
            a = int(top_rgba[3] * (1 - t) + bottom_rgba[3] * t)
            draw.line([(0, y), (w, y)], fill=(r, g, b, a))
        return grad

    def _cover_image(self, album_img: Image.Image, target_px: int) -> Image.Image:
        """Square fit album art centered."""
        return ImageOps.fit(
            album_img.convert("RGBA"),
            (target_px, target_px),
            method=Image.LANCZOS,
            centering=(0.5, 0.5),
        )

    def _get_font_metrics(self, font: ImageFont.FreeTypeFont) -> dict[str, int]:
        """
        Get actual rendered metrics for a font.
        Returns: {"height": int, "ascent": int, "descent": int, "line_height": int}
        Uses cache to avoid repeated calculations.
        """
        font_id = id(font)
        if font_id in self._font_metrics_cache:
            return self._font_metrics_cache[font_id]
        
        # Measure sample text to get bounding box
        try:
            # Use diverse characters to get representative metrics
            sample_text = "Tgj123日本語"
            bbox = font.getbbox(sample_text)
            # bbox: (left, top, right, bottom)
            height = bbox[3] - bbox[1]
            ascent = max(0, -bbox[1])  # Distance from baseline to top
            descent = max(0, bbox[3])  # Distance from baseline to bottom
        except Exception:
            # Fallback: use font size as estimate
            height = int(font.size) if hasattr(font, "size") else 16
            ascent = int(height * 0.8)
            descent = int(height * 0.2)
        
        metrics = {
            "height": height,
            "ascent": ascent,
            "descent": descent,
            "line_height": height,
        }
        self._font_metrics_cache[font_id] = metrics
        return metrics

    def _is_cjk_font(self, font: ImageFont.FreeTypeFont) -> bool:
        """
        Check if a font is one of our CJK variants.
        """
        for lang_fonts in self._font_fallback_variants.values():
            if font in lang_fonts:
                return True
        return False

    # -------------------------------------------------------------------------
    # Text rendering
    # -------------------------------------------------------------------------

    def _get_alignment(self) -> str:
        return self._text_alignment_portrait if self._orientation == "portrait" else self._text_alignment_landscape

    def _add_text(
        self,
        image: Image.Image,
        title: str,
        subtitle: str,
        canvas_w: int,
        canvas_h: int,
        meta: str = "",
    ) -> None:
        alignment = self._get_alignment()

        # Get appropriate fonts based on text content
        meta_font, meta_fallback, meta_lang = self._select_font_for_text(meta, is_title=False)
        subtitle_font, subtitle_fallback, subtitle_lang = self._select_font_for_text(subtitle, is_title=False)
        title_font, title_fallback, title_lang = self._select_font_for_text(title, is_title=True)

        # Get metrics for proper sizing
        meta_metrics = self._get_font_metrics(meta_fallback or meta_font)
        subtitle_metrics = self._get_font_metrics(subtitle_fallback or subtitle_font)
        title_metrics = self._get_font_metrics(title_fallback or title_font)

        # Auto-adjust line spacing based on CJK detection
        # CJK fonts benefit from slightly more spacing
        base_line_spacing = self._line_spacing_px
        meta_line_spacing = base_line_spacing + (2 if meta_lang != "en" else 0)
        subtitle_line_spacing = base_line_spacing + (2 if subtitle_lang != "en" else 0)
        title_line_spacing = base_line_spacing + (2 if title_lang != "en" else 0)

        # Optional meta line (draws first; appears lowest on screen after stacking)
        meta_position_y = canvas_h - (self._get_text_offset_bottom_px() + meta_metrics["height"])
        meta_block_h = 0
        if meta:
            meta_block_h = self._draw_text(
                image=image,
                text=meta,
                text_color="white",
                font=meta_font,
                fallback_font=meta_fallback,
                draw_position_y=meta_position_y,
                canvas_w=canvas_w,
                alignment=alignment,
                font_metrics=meta_metrics,
                line_spacing=meta_line_spacing,
            )

        # Subtitle (e.g., artist or Temp � Feels like)
        subtitle_position_y = (
            canvas_h
            - (self._get_text_offset_bottom_px() + subtitle_metrics["height"])
            - meta_block_h
        )
        subtitle_block_h = self._draw_text(
            image=image,
            text=subtitle,
            text_color="white",
            font=subtitle_font,
            fallback_font=subtitle_fallback,
            draw_position_y=subtitle_position_y,
            canvas_w=canvas_w,
            alignment=alignment,
            font_metrics=subtitle_metrics,
            line_spacing=subtitle_line_spacing,
        )

        # Title (e.g., song title or weather description)
        title_position_y = (
            canvas_h
            - (self._get_text_offset_bottom_px() + title_metrics["height"])
            - meta_block_h
            - subtitle_block_h
        )
        self._draw_text(
            image=image,
            text=title,
            text_color="white",
            font=title_font,
            fallback_font=title_fallback,
            draw_position_y=title_position_y,
            canvas_w=canvas_w,
            alignment=alignment,
            font_metrics=title_metrics,
            line_spacing=title_line_spacing,
        )

    def _draw_text(
        self,
        image: Image.Image,
        text: str,
        text_color: str,
        font: ImageFont.FreeTypeFont,
        fallback_font: Optional[ImageFont.FreeTypeFont],
        draw_position_y: int,
        canvas_w: int,
        alignment: str,
        font_metrics: Optional[dict[str, int]] = None,
        line_spacing: Optional[int] = None,
    ) -> int:
        # Available width considers left/right offsets and shadow shift
        available_width = (
            canvas_w
            - self._get_text_offset_left_px()
            - self._get_text_offset_right_px()
            - self._get_text_shadow_px()
        )
        lines = self._break_text_to_lines_advanced(
            text=text,
            max_width=available_width,
            font=font,
            fallback_font=fallback_font,
            break_long_words=self._wrap_break_long_words,
            hyphenate=self._wrap_hyphenate,
        )

        # Use provided metrics or calculate
        if font_metrics is None:
            font_metrics = self._get_font_metrics(fallback_font or font)
        if line_spacing is None:
            line_spacing = self._line_spacing_px

        draw = ImageDraw.Draw(image)
        font_height = font_metrics["height"]

        # If multiple lines, shift the starting Y upward so the block remains anchored bottom
        if len(lines) > 1:
            draw_position_y -= (len(lines) - 1) * (font_height + line_spacing)

        total_height = 0
        for line in lines:
            line_w = self._text_width(line, font, fallback_font, draw)
            if alignment == "center":
                x = self._get_text_offset_left_px() + max(0, (available_width - line_w) // 2)
            elif alignment == "right":
                x = canvas_w - self._get_text_offset_right_px() - line_w
            else:
                x = self._get_text_offset_left_px()

            # Optional soft shadow (down-right)
            if self._get_text_shadow_px() > 0:
                self._draw_line_with_fallback(
                    draw=draw,
                    line=line,
                    x_start=x + self._get_text_shadow_px(),
                    y=draw_position_y + self._get_text_shadow_px(),
                    font=font,
                    fallback_font=fallback_font,
                    fill="black",
                )

            self._draw_line_with_fallback(
                draw=draw,
                line=line,
                x_start=x,
                y=draw_position_y,
                font=font,
                fallback_font=fallback_font,
                fill=text_color,
            )
            draw_position_y += font_height + line_spacing
            total_height += font_height + line_spacing

        if total_height > 0:
            total_height -= line_spacing
        return total_height

    @staticmethod
    def _break_text_to_lines_advanced(
        text: str,
        max_width: int,
        font: ImageFont.FreeTypeFont,
        fallback_font: Optional[ImageFont.FreeTypeFont],
        break_long_words: bool = True,
        hyphenate: bool = False,
    ) -> list[str]:
        draw = ImageDraw.Draw(Image.new("RGB", (max_width, 1)))
        words = text.split()
        if not words:
            return []

        lines: list[str] = []
        current: list[str] = []

        def width_of(tokens: list[str]) -> int:
            return int(
                DisplayService._text_width(
                    " ".join(tokens),
                    font=font,
                    fallback_font=fallback_font,
                    draw=draw,
                )
            )

        for word in words:
            candidate = current + [word]
            if width_of(candidate) <= max_width:
                current.append(word)
                continue

            # Long word breaking when a single word exceeds max width
            if break_long_words and int(
                DisplayService._text_width(word, font=font, fallback_font=fallback_font, draw=draw)
            ) > max_width:
                if current:
                    lines.append(" ".join(current))
                    current = []
                segment = ""
                for ch in word:
                    next_seg = segment + ch
                    if int(
                        DisplayService._text_width(
                            next_seg,
                            font=font,
                            fallback_font=fallback_font,
                            draw=draw,
                        )
                    ) <= max_width:
                        segment = next_seg
                    else:
                        # Optionally hyphenate
                        if hyphenate and segment:
                            hyphenated = segment + "-"
                            if int(
                                DisplayService._text_width(
                                    hyphenated,
                                    font=font,
                                    fallback_font=fallback_font,
                                    draw=draw,
                                )
                            ) <= max_width:
                                lines.append(hyphenated)
                            else:
                                lines.append(segment)
                        else:
                            lines.append(segment)
                        segment = ch
                if segment:
                    current = [segment]
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]

        if current:
            lines.append(" ".join(current))
        return lines

    @staticmethod
    def _draw_line_with_fallback(
        draw: ImageDraw.ImageDraw,
        line: str,
        x_start: int,
        y: int,
        font: ImageFont.FreeTypeFont,
        fallback_font: Optional[ImageFont.FreeTypeFont],
        fill: str,
    ) -> None:
        cursor_x = x_start
        for ch in line:
            use_font = DisplayService._select_font_for_char(ch, font, fallback_font)
            draw.text((cursor_x, y), ch, font=use_font, fill=fill)
            cursor_x += int(draw.textlength(ch, font=use_font))

    @staticmethod
    def _text_width(
        text: str,
        font: ImageFont.FreeTypeFont,
        fallback_font: Optional[ImageFont.FreeTypeFont],
        draw: Optional[ImageDraw.ImageDraw] = None,
    ) -> int:
        if draw is None:
            dummy_w = max(1, int(font.size) if hasattr(font, "size") else 100)
            draw = ImageDraw.Draw(Image.new("RGB", (dummy_w, 1)))

        width = 0
        for ch in text:
            use_font = DisplayService._select_font_for_char(ch, font, fallback_font)
            width += int(draw.textlength(ch, font=use_font))
        return width

    def _select_font_for_text(
        self, text: str, is_title: bool
    ) -> Tuple[ImageFont.FreeTypeFont, Optional[ImageFont.FreeTypeFont], str]:
        """
        Select appropriate fonts (main, fallback) based on text content.
        Detects CJK language and returns the appropriate font variant.
        Returns: (main_font, fallback_font or None, detected_language)
        """
        main_font = self._font_title if is_title else self._font_subtitle
        
        # Check if text contains CJK characters
        has_cjk = any(self._is_cjk(ch) for ch in text)
        if not has_cjk or not self._font_fallback_variants:
            return (main_font, None, "en")
        
        # Detect language and get appropriate fallback font
        lang = self._detect_cjk_language(text)
        if lang in self._font_fallback_variants:
            fallback_font = self._font_fallback_variants[lang][0 if is_title else 1]
            return (main_font, fallback_font, lang)
        
        # Fallback to Japanese if detected language not available
        if "ja" in self._font_fallback_variants:
            fallback_font = self._font_fallback_variants["ja"][0 if is_title else 1]
            return (main_font, fallback_font, "ja")
        
        return (main_font, None, "en")

    @staticmethod
    def _select_font_for_char(
        ch: str, font: ImageFont.FreeTypeFont, fallback_font: Optional[ImageFont.FreeTypeFont]
    ) -> ImageFont.FreeTypeFont:
        """Select appropriate font for a character, using fallback for CJK."""
        if fallback_font and DisplayService._is_cjk(ch):
            return fallback_font
        return font

    @staticmethod
    def _is_cjk(ch: str) -> bool:
        """
        Check if a character is a CJK (Chinese/Japanese/Korean) character
        that may require a fallback font.
        """
        if not ch:
            return False
        code = ord(ch)
        return (
            # Japanese
            0x3040 <= code <= 0x309F   # Hiragana
            or 0x30A0 <= code <= 0x30FF   # Katakana
            or 0x31F0 <= code <= 0x31FF   # Katakana Phonetic Extensions
            # Chinese/Japanese/Korean Ideographs
            or 0x4E00 <= code <= 0x9FFF   # CJK Unified Ideographs
            or 0x3400 <= code <= 0x4DBF   # CJK Unified Ideographs Extension A
            or 0x20000 <= code <= 0x2A6DF # CJK Unified Ideographs Extension B
            or 0x2A700 <= code <= 0x2B73F # CJK Unified Ideographs Extension C
            or 0x2B740 <= code <= 0x2B81F # CJK Unified Ideographs Extension D
            or 0x2B820 <= code <= 0x2CEAF # CJK Unified Ideographs Extension E
            or 0x2CEB0 <= code <= 0x2EBEF # CJK Unified Ideographs Extension F
            or 0xF900 <= code <= 0xFAFF   # CJK Compatibility Ideographs
            or 0x2F800 <= code <= 0x2FA1F # CJK Compatibility Ideographs Supplement
            # Chinese Phonetic
            or 0x3100 <= code <= 0x312F   # Bopomofo
            or 0x31A0 <= code <= 0x31BF   # Bopomofo Extended
            # Korean
            or 0xAC00 <= code <= 0xD7AF   # Hangul Syllables
            or 0x1100 <= code <= 0x11FF   # Hangul Jamo
            or 0x3130 <= code <= 0x318F   # Hangul Compatibility Jamo
            or 0xA960 <= code <= 0xA97F   # Hangul Jamo Extended-A
            or 0xD7B0 <= code <= 0xD7FF   # Hangul Jamo Extended-B
            # CJK Symbols and Punctuation
            or 0x3000 <= code <= 0x303F   # CJK Symbols and Punctuation
            # Full-width forms
            or 0xFF00 <= code <= 0xFFEF   # Halfwidth and Fullwidth Forms
        )

    @staticmethod
    def _detect_cjk_language(text: str) -> str:
        """
        Detect which CJK language is predominant in the text.
        Returns: 'ja' (Japanese), 'ko' (Korean), or 'zh-Hant' (Traditional Chinese).
        Defaults to 'ja' when no language-specific marker is detected.
        """
        if not text:
            return "ja"

        # Count language-specific characters
        hiragana_count = 0
        katakana_count = 0
        hangul_count = 0
        bopomofo_count = 0

        for ch in text:
            code = ord(ch)
            if 0x3040 <= code <= 0x309F:  # Hiragana
                hiragana_count += 1
            elif 0x30A0 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:  # Katakana
                katakana_count += 1
            elif 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:  # Hangul
                hangul_count += 1
            elif 0x3100 <= code <= 0x312F or 0x31A0 <= code <= 0x31BF:  # Bopomofo
                bopomofo_count += 1

        if hangul_count > 0:
            return "ko"
        if hiragana_count > 0 or katakana_count > 0:
            return "ja"
        if bopomofo_count > 0:
            return "zh-Hant"

        return "ja"

    # ---------------------------------------------------------------------
    # Display output
    # ---------------------------------------------------------------------

    def _show_image_on_display(self, image: Image.Image, saturation: float = 0.5, show_ai_dot: bool = False) -> None:
        try:
            image = self._finalize_for_hardware(image)

            # Optionally draw a small red indicator at the hardware top-left
            if show_ai_dot:
                try:
                    draw = ImageDraw.Draw(image)
                    # Draw in final hardware coordinates so margins are interpreted consistently.
                    w, h = image.size
                    short = min(w, h)
                    margin_x, margin_y = self._get_runtime_ai_dot_margins()

                    # Radius scales with shorter edge to remain consistent visually
                    radius = max(10, short // 50)

                    # Keep the full circle on-screen even if margins are set too large.
                    x0 = max(0, min(margin_x, w - (radius * 2)))
                    y0 = max(0, min(margin_y, h - (radius * 2)))
                    x1 = x0 + (radius * 2)
                    y1 = y0 + (radius * 2)
                    draw.ellipse((x0, y0, x1, y1), fill=(255, 0, 0))
                except Exception:
                    # Non-fatal drawing error; continue to display image
                    pass

            # Save the exact final frame for the web management preview.
            try:
                os.makedirs(os.path.dirname(self._preview_image_path), exist_ok=True)
                image.save(self._preview_image_path, format="PNG")
            except Exception as e:
                self._logger.warning(f"Could not persist display preview image: {e}")

            self._inky.set_image(image, saturation=saturation)
            self._inky.show()
        except Exception as e:
            self._logger.error(f"Error displaying image: {e}")
            self._logger.error(traceback.format_exc())

    def _make_fallback_background(self) -> Image.Image:
        canvas_w, canvas_h = self._canvas_size()
        return Image.new("RGBA", (canvas_w, canvas_h), color=(0, 0, 0, 255))

    def _get_runtime_orientation(self) -> str:
        """
        Read the current orientation from the shared settings database.
        Falls back to self._orientation if the toggle state is missing or unreadable.
        """
        try:
            from settings_store import SettingsStore

            toggle_state = SettingsStore().load_toggle_state()
            o = toggle_state.get("orientation", self._orientation)
            return o.lower() if o else self._orientation
        except Exception:
            pass
        return self._orientation

    def _get_runtime_ai_dot_margins(self) -> Tuple[int, int]:
        """
        Read AI-dot margins from the shared settings database so admin changes
        are reflected without requiring a process restart.
        """
        try:
            from settings_store import SettingsStore

            cfg = SettingsStore().load_config()
            dcfg = cfg.get("display", {}) if isinstance(cfg.get("display", {}), dict) else {}
            margin_x = int(dcfg.get("ai_dot_margin_x_px", self._ai_dot_margin_x_px))
            margin_y = int(dcfg.get("ai_dot_margin_y_px", self._ai_dot_margin_y_px))
            return max(0, margin_x), max(0, margin_y)
        except Exception:
            return max(0, int(self._ai_dot_margin_x_px)), max(0, int(self._ai_dot_margin_y_px))

    # ---------------------------------------------------------------------
    # Utilities
    # ---------------------------------------------------------------------

    def _safe_text(self, value: Optional[str]) -> str:
        """Normalize None, strip whitespace, and ensure printable text."""
        if value is None:
            return ""
        text = str(value).strip()
        return "".join(ch for ch in text if ch.isprintable())

    def _fetch_image(self, url: Optional[str], timeout: float = 6.0) -> Optional[Image.Image]:
        """Fetch an image from URL with timeout and error handling."""
        if not url:
            return None
        try:
            r = self._http.get(url, timeout=timeout)
            r.raise_for_status()
            img = Image.open(BytesIO(r.content)).convert("RGBA")
            return ImageOps.exif_transpose(img)
        except Exception as e:
            self._logger.debug(f"Image fetch failed: {e}")
           
