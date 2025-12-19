
import logging
import time
import traceback
from io import BytesIO
from typing import Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

from service.weather_service import WeatherInfo
from service.song_identify_service import SongInfo

from inky.auto import auto
from inky.inky_uc8159 import CLEAN

# --- Optional: Spotify artist image fallback ---
try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
except Exception:
    spotipy = None
    SpotifyClientCredentials = None


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

        # Inky hardware
        self._inky = auto()

        # HTTP session for image fetches (reuses connections)
        self._http = requests.Session()

        # Orientation & rotation (defaults; runtime overrides come from toggle_state)
        dcfg = self._config.get("display", {})
        self._orientation = "portrait"
        self._rotation = 90

        # Weather background (file path optional)
        self._weather_bg_path = dcfg.get("weather_background_image") or dcfg.get("screensaver_image")

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
        fpath = dcfg.get("font_path")
        fsize_title = int(dcfg.get("font_size_title", 48))
        fsize_subtitle = int(dcfg.get("font_size_subtitle", 32))
        try:
            if not fpath:
                raise ValueError("Font path missing in config.display.font_path")
            self._font_title = ImageFont.truetype(fpath, fsize_title)
            self._font_subtitle = ImageFont.truetype(fpath, fsize_subtitle)
        except Exception as e:
            # Fallback to default bitmap font if truetype fails
            self._logger.warning(f"Falling back to default font: {e}")
            self._font_title = ImageFont.load_default()
            self._font_subtitle = ImageFont.load_default()

        # Optional Spotify client credentials (for artist image lookup)
        scfg = self._config.get("spotify", {})
        self._spotify_id = scfg.get("client_id")
        self._spotify_secret = scfg.get("client_secret")
        self._spotify = None
        if spotipy and self._spotify_id and self._spotify_secret:
            try:
                self._spotify = spotipy.Spotify(
                    auth_manager=SpotifyClientCredentials(
                        client_id=self._spotify_id, client_secret=self._spotify_secret
                    )
                )
            except Exception as e:
                self._logger.warning(f"Spotify client init failed: {e}")

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
        rotation: Optional[int] = None,
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

        if rotation is not None:
            self._rotation = int(rotation)

        self._orientation = orientation
        self._logger.info(
            "Orientation changed to %s (rotation=%s°)",
            self._orientation,
            self._rotation,
        )

    def update_display_to_playing(self, song_info: SongInfo) -> None:
        """
        Render 'playing' screen: backdrop (artist or blurred album), album cover,
        title (song), subtitle (artist), and meta (album + year).
        """
        # Album art with guard + fallback
        album_cover_image: Image.Image = (
            self._fetch_image(getattr(song_info, "album_art", None)) or
            self._make_fallback_background().convert("RGBA")
        )

        # Build meta line (album (year) | album | year)
        album_meta = ""
        if (song_info.album or "") or (song_info.release_year or ""):
            if (song_info.album or "") and (song_info.release_year or ""):
                album_meta = f"{song_info.album} ({song_info.release_year})"
            else:
                album_meta = (song_info.album or "") or (song_info.release_year or "")

        # Try artist image as backdrop; fall back to album blurred/darkened
        artist_backdrop_img = self._get_artist_backdrop_image(song_info)
        display_image = self._generate_display_image(
            base_image=album_cover_image,
            title=song_info.title or "",
            subtitle=song_info.artist or "",
            mode="playing",
            
            meta=album_meta,
            artist_backdrop=artist_backdrop_img,
        )
        self._show_image_on_display(display_image, show_ai_dot=False)



    def update_display_to_screensaver(self, weather_info: WeatherInfo, show_ai_dot: bool = False, fallback_image_path: Optional[str] = None) -> None:
        """
        Render screensaver/weather screen.
        """
    # Background image (file) with fallback

        # If a specific fallback path override was provided (e.g., time-relevant AI fallback), prefer it
        chosen_path = fallback_image_path or self._weather_bg_path
        if chosen_path:
            try:
                bg_image = Image.open(chosen_path).convert("RGBA")
            except Exception as e:
                self._logger.error(
                    f"Failed to load weather background '{chosen_path}': {e}"
                )
                bg_image = self._make_fallback_background().convert("RGBA")
        else:
            bg_image = self._make_fallback_background().convert("RGBA")


    # Safe text extraction
        temp        = self._safe_text(getattr(weather_info, "temperature", None))
        raw_sub = self._safe_text(getattr(weather_info, "sub_description", None))
        parts = [p.strip() for p in raw_sub.split(".") if p.strip()]
        if parts and parts[0].lower().startswith("feels like"):
            parsed_feels = parts[0]                     # e.g., "Feels like 13�C"
            parsed_desc  = ". ".join(parts[1:])         # e.g., "Overcast Clouds"
        if parsed_desc:
            parsed_desc += "."

    # Decide what goes on each line
        title    = temp         # Top line
        subtitle = parsed_desc # Second line
        meta     = parsed_feels   # Third line

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
        """Rotate canvas based on configured rotation degrees."""
        if self._rotation:
            return image.rotate(self._rotation, expand=False)
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

    # ---------------------------------------------------------------------
    # Text rendering
    # ---------------------------------------------------------------------

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

        # Optional meta line (draws first; appears lowest on screen after stacking)
        meta_position_y = canvas_h - (self._get_text_offset_bottom_px() + self._font_subtitle.size)
        meta_block_h = 0
        if meta:
            meta_block_h = self._draw_text(
                image=image,
                text=meta,
                text_color="white",
                font=self._font_subtitle,
                draw_position_y=meta_position_y,
                canvas_w=canvas_w,
                alignment=alignment,
            )

        # Subtitle (e.g., artist or Temp � Feels like)
        subtitle_position_y = (
            canvas_h
            - (self._get_text_offset_bottom_px() + self._font_subtitle.size)
            - meta_block_h
        )
        subtitle_block_h = self._draw_text(
            image=image,
            text=subtitle,
            text_color="white",
            font=self._font_subtitle,
            draw_position_y=subtitle_position_y,
            canvas_w=canvas_w,
            alignment=alignment,
        )

        # Title (e.g., song title or weather description)
        title_position_y = (
            canvas_h
            - (self._get_text_offset_bottom_px() + self._font_title.size)
            - meta_block_h
            - subtitle_block_h
        )
        self._draw_text(
            image=image,
            text=title,
            text_color="white",
            font=self._font_title,
            draw_position_y=title_position_y,
            canvas_w=canvas_w,
            alignment=alignment,
        )

    def _draw_text(
        self,
        image: Image.Image,
        text: str,
        text_color: str,
        font: ImageFont.FreeTypeFont,
        draw_position_y: int,
        canvas_w: int,
        alignment: str,
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
            break_long_words=self._wrap_break_long_words,
            hyphenate=self._wrap_hyphenate,
        )

        draw = ImageDraw.Draw(image)
        font_size = font.size if hasattr(font, "size") else 16  # default fallback

        # If multiple lines, shift the starting Y upward so the block remains anchored bottom
        if len(lines) > 1:
            draw_position_y -= (len(lines) - 1) * (font_size + self._line_spacing_px)

        total_height = 0
        for line in lines:
            line_w = int(draw.textlength(line, font=font))
            if alignment == "center":
                x = self._get_text_offset_left_px() + max(0, (available_width - line_w) // 2)
            elif alignment == "right":
                x = canvas_w - self._get_text_offset_right_px() - line_w
            else:
                x = self._get_text_offset_left_px()

            # Optional soft shadow (down-right)
            if self._get_text_shadow_px() > 0:
                draw.text(
                    (x + self._get_text_shadow_px(), draw_position_y + self._get_text_shadow_px()),
                    line,
                    font=font,
                    fill="black",
                )

            draw.text((x, draw_position_y), line, font=font, fill=text_color)
            draw_position_y += font_size + self._line_spacing_px
            total_height += font_size + self._line_spacing_px

        if total_height > 0:
            total_height -= self._line_spacing_px
        return total_height

    @staticmethod
    def _break_text_to_lines_advanced(
        text: str,
        max_width: int,
        font: ImageFont.FreeTypeFont,
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
            return int(draw.textlength(" ".join(tokens), font=font))

        for word in words:
            candidate = current + [word]
            if width_of(candidate) <= max_width:
                current.append(word)
                continue

            # Long word breaking when a single word exceeds max width
            if break_long_words and int(draw.textlength(word, font=font)) > max_width:
                if current:
                    lines.append(" ".join(current))
                    current = []
                segment = ""
                for ch in word:
                    next_seg = segment + ch
                    if int(draw.textlength(next_seg, font=font)) <= max_width:
                        segment = next_seg
                    else:
                        # Optionally hyphenate
                        if hyphenate and segment:
                            hyphenated = segment + "-"
                            if int(draw.textlength(hyphenated, font=font)) <= max_width:
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
                    # small, clearly visible dot with a modest margin
                    margin = max(4, min(image.size) // 64)
                    radius = max(3, min(image.size) // 80)
                    x0 = margin
                    y0 = margin
                    x1 = x0 + (radius * 2)
                    y1 = y0 + (radius * 2)
                    draw.ellipse((x0, y0, x1, y1), fill=(255, 0, 0))
                except Exception:
                    # Non-fatal drawing error; continue to display image
                    pass

            self._inky.set_image(image, saturation=saturation)
            self._inky.show()
        except Exception as e:
            self._logger.error(f"Error displaying image: {e}")
            self._logger.error(traceback.format_exc())

    def _make_fallback_background(self) -> Image.Image:
        canvas_w, canvas_h = self._canvas_size()
        return Image.new("RGBA", (canvas_w, canvas_h), color=(0, 0, 0, 255))

    # ---------------------------------------------------------------------
    # Artist backdrop helpers
    # ---------------------------------------------------------------------

    def _get_artist_backdrop_image(self, song_info: SongInfo) -> Optional[Image.Image]:
        """
        Try Spotify artist image by name; returns RGBA PIL Image or None.
        If Spotify creds are missing/unavailable, returns None.
        """
        if not (self._spotify and song_info and (song_info.artist or "")):
            return None
        try:
            results = self._spotify.search(
                q=f'artist:"{song_info.artist}"', type="artist", limit=1
            )
            artists = results.get("artists", {}).get("items", [])
            if not artists:
                return None

            images = artists[0].get("images", [])
            if not images:
                return None

            url = images[0].get("url")
            if not url:
                return None

            resp = self._http.get(url, timeout=6.0)
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content)).convert("RGBA")
            img = ImageOps.exif_transpose(img)  # normalize orientation
            return img
        except Exception as e:
            self._logger.debug(f"No Spotify artist image: {e}")
            return None

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
           
