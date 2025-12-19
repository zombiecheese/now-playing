
import logging
import sys
import os
import json
import numpy as np
import traceback
import signal
import time
from typing import Tuple, Final, Optional
import gpiod
import gpiodevice
from gpiod.line import Bias, Direction, Edge
import threading

# Local imports
from logger import Logger
from config import Config
from state_manager import StateManager, DisplayState
from service.song_identify_service import SongIdentifyService, SongInfo
from audio_processing_utils import AudioProcessingUtils  # <-- fixed typo
from service.audio_recording_service import AudioRecordingService
from service.music_detection_service import MusicDetectionService
from service.weather_service import WeatherService, WeatherInfo
from service.display_service import DisplayService
from service.spotify_service import SpotifyService
from service.ai_background_service import AIBackgroundService

class NowPlaying:
    # Audio & model settings
    AUDIO_DEVICE_SAMPLING_RATE: Final[int] = 44100
    AUDIO_DEVICE_NUMBER_OF_CHANNELS: Final[int] = 1

    # Default recording duration (used if YAML value missing/invalid)
    DEFAULT_AUDIO_RECORDING_DURATION_IN_SECONDS: Final[int] = 6

    SUPPORTED_SAMPLING_RATE_BY_MUSIC_DETECTION_MODEL: Final[int] = 16000

    # Physical buttons
    BUTTONS = [5, 6, 16, 24]
    LABELS = ["A", "B", "C", "D"]
    INPUT = gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP, edge_detection=Edge.FALLING)

    def __init__(self) -> None:
        # Handle clean exit signals
        signal.signal(signal.SIGTERM, self._handle_exit)  # System/process termination
        signal.signal(signal.SIGINT, self._handle_exit)   # Ctrl+C

        # Config & logging
        self._config: dict = Config().get_config()
        self._logger: logging.Logger = Logger().get_logger()

        # ----- Read audio recording duration from YAML with fallback -----
        cfg_audio = self._config.get("audio", {})
        try:
            self._audio_recording_duration: int = int(
                cfg_audio.get(
                    "recording_duration_seconds",
                    NowPlaying.DEFAULT_AUDIO_RECORDING_DURATION_IN_SECONDS,
                )
            )
        except (ValueError, TypeError):
            self._audio_recording_duration = NowPlaying.DEFAULT_AUDIO_RECORDING_DURATION_IN_SECONDS

        # Defensive bounds check (adjust max to taste)
        if self._audio_recording_duration <= 0 or self._audio_recording_duration > 30:
            self._logger.warning(
                f"Invalid audio.recording_duration_seconds={self._audio_recording_duration}; "
                f"falling back to {NowPlaying.DEFAULT_AUDIO_RECORDING_DURATION_IN_SECONDS}s"
            )
            self._audio_recording_duration = NowPlaying.DEFAULT_AUDIO_RECORDING_DURATION_IN_SECONDS

        # Core services
        self._audio_recording_service: AudioRecordingService = AudioRecordingService(
            sampling_rate=NowPlaying.AUDIO_DEVICE_SAMPLING_RATE,
            channels=NowPlaying.AUDIO_DEVICE_NUMBER_OF_CHANNELS
        )
        self._music_detection_service: MusicDetectionService = MusicDetectionService(
            audio_duration_in_seconds=self._audio_recording_duration
        )
        self._song_identify_service: SongIdentifyService = SongIdentifyService()
        self._weather_service: WeatherService = WeatherService()  # 15-min cache TTL by default
        self._display_service: DisplayService = DisplayService()
        self._spotify_service: SpotifyService = SpotifyService()
        self._state_manager: StateManager = StateManager()

        # NEW: OpenAI background generator (uses weather.background_refresh_seconds from config)
        self._ai_bg: AIBackgroundService = AIBackgroundService()
        # When True: force using AI background fallback images (no generation)
        self._ai_bg_fallback_mode: bool = False
        self._toggle_state_mtime: Optional[float] = None
        # Display orientation (portrait or landscape) defaults; toggle_state overrides
        self._orientation: str = "portrait"
        self._portrait_rotate_degrees: int = 90
        self._landscape_rotate_degrees: int = 0
        # Ensure toggle state file exists with defaults
        self._ensure_toggle_state_file_exists()
        # Load persisted toggle state (if present)
        try:
            self._load_toggle_state_from_file()
        except Exception:
            pass
        self._toggle_state_mtime = self._get_toggle_state_mtime()

        # Initial housekeeping
        self._clean_display_and_set_clean_state()
        self._setup_buttons()
        self._start_button_listener()

    def run(self) -> None:
        while True:
            try:
                audio, is_music_detected = self._record_audio_and_detect_music()
                if is_music_detected:
                    self._handle_music_detected(audio)
                else:
                    self._handle_no_music_detected()
            except Exception as e:
                self._logger.error(f"Error occurred: {e}")
                self._logger.error(traceback.format_exc())

    # --- Audio pipeline ---
    def _record_audio_and_detect_music(self) -> Tuple[np.ndarray, bool]:
        audio = self._audio_recording_service.record(
            duration=self._audio_recording_duration
        )
        resampled_audio = AudioProcessingUtils.resample(
            audio,
            source_sampling_rate=NowPlaying.AUDIO_DEVICE_SAMPLING_RATE,
            target_sampling_rate=NowPlaying.SUPPORTED_SAMPLING_RATE_BY_MUSIC_DETECTION_MODEL
        )
        is_music_detected = self._music_detection_service.is_music_detected(resampled_audio)
        return audio, is_music_detected

    def _handle_music_detected(self, audio: np.ndarray) -> None:
        song_info = self._trigger_song_identify(audio)
        if (
            song_info
            and (
                self._state_manager.get_state().current != DisplayState.PLAYING
                or self._state_manager.music_still_playing_but_different_song_identified(song_info.title)
            )
        ):
            self._set_playing_state_and_update_display(song_info)
        self._state_manager.update_last_music_detected_time()

    def _trigger_song_identify(self, audio: np.ndarray) -> SongInfo:
        int16_audio = AudioProcessingUtils.float32_to_int16(audio)
        wav_audio = AudioProcessingUtils.to_wav(
            int16_audio,
            sampling_rate=NowPlaying.AUDIO_DEVICE_SAMPLING_RATE
        )
        # Use the synchronous wrapper to call the async identify implementation
        return self._song_identify_service.identify_sync(wav_audio)

    def _set_playing_state_and_update_display(self, song_info: SongInfo) -> None:
        self._refresh_toggle_state_if_changed()
        if self._state_manager.should_clean_display():
            self._clean_display_and_set_clean_state()
        self._state_manager.set_playing_state(song_info.title, song_info.artist)
        self._display_service.update_display_to_playing(song_info)
        self._state_manager.increase_image_counter()

    # --- No music -> screensaver/weather ---
    def _handle_no_music_detected(self) -> None:
        """
        When no music is detected:
        1) Opportunistically refresh the AI background image if its TTL has expired.
        2) If screensaver should be shown/updated (first time or stale), fetch weather and render.
        """
        # (1) Optionally generate a fresh AI background image (if not forcing fallback).
        if not self._ai_bg_fallback_mode:
            try:
                self._ai_bg.refresh_background_if_needed()
            except Exception as e:
                # Non-fatal: we still proceed with weather rendering
                self._logger.warning(f"AI background refresh skipped: {e}")
        else:
            self._logger.info("AI background generation is currently forced off; using fallbacks.")

        # (2) Decide whether to (re)render screensaver with up-to-date weather info
        if (
            self._state_manager.get_state().current != DisplayState.SCREENSAVER
            and self._state_manager.no_music_detected_for_more_than_a_minute()
        ) or self._state_manager.screensaver_still_up_but_weather_info_outdated():
            weather_info = self._weather_service.get_weather_info()
            # Determine whether to force a time-relevant fallback image and show the indicator dot
            fallback_path = None
            show_dot = False
            try:
                if self._ai_bg_fallback_mode:
                    fallback_path = self._ai_bg.get_fallback_path()
                    if fallback_path:
                        show_dot = True
            except Exception:
                fallback_path = None
            self._set_screensaver_state_and_update_display(weather_info, show_ai_dot=show_dot, fallback_image_path=fallback_path)

    def _set_screensaver_state_and_update_display(self, weather_info: WeatherInfo, show_ai_dot: bool = False, fallback_image_path: Optional[str] = None) -> None:
        self._refresh_toggle_state_if_changed()
        if self._state_manager.should_clean_display():
            self._clean_display_and_set_clean_state()
        self._state_manager.set_screensaver_state(weather_info)
        # Pass through whether to render the small red dot and an optional fallback image override
        self._display_service.update_display_to_screensaver(weather_info, show_ai_dot=show_ai_dot, fallback_image_path=fallback_image_path)
        self._state_manager.increase_image_counter()

    # --- Buttons & housekeeping ---
    @staticmethod
    def _handle_exit(_sig, _frame):
        sys.exit(0)

    def _clean_display_and_set_clean_state(self) -> None:
        self._display_service.clean_display()
        self._state_manager.set_clean_state()

    def _setup_buttons(self) -> None:
        chip = gpiodevice.find_chip_by_platform()
        self.OFFSETS = [chip.line_offset_from_id(id) for id in NowPlaying.BUTTONS]
        line_config = dict.fromkeys(self.OFFSETS, NowPlaying.INPUT)
        self.request = chip.request_lines(consumer="inky7-buttons", config=line_config)

    def _start_button_listener(self) -> None:
        def listen():
            while True:
                for event in self.request.read_edge_events():
                    index = self.OFFSETS.index(event.line_offset)
                    button_label = NowPlaying.LABELS[index]
                    self._logger.debug(f"Button {button_label} pressed")
                    if button_label == "A":
                        self._handle_button_a()
                    elif button_label == "B":
                        self._handle_button_b()
                    elif button_label == "C":
                        self._handle_button_c()

        threading.Thread(target=listen, daemon=True).start()

    def _handle_button_a(self) -> None:
        """Add the currently playing track to the configured Spotify playlist."""
        try:
            if not self._state_manager.get_state().current == DisplayState.PLAYING:
                return
            title = self._state_manager.get_playing_state().song_title
            artist = self._state_manager.get_playing_state().song_artist
            track_uri = self._spotify_service.search_track_uri(title, artist)
            if track_uri:
                self._spotify_service.add_to_playlist(track_uri)
        except Exception as e:
            self._logger.error(f"Error occurred: {e}")
            self._logger.error(traceback.format_exc())

    def _handle_button_b(self) -> None:
        """Toggle AI background generation fallback mode (force using time-relevant fallbacks).

        When enabled we skip generation and use the configured time-relevant fallback image.
        A small red dot will be shown on the screensaver when the fallback image is available.
        """
        try:
            prev = bool(self._ai_bg_fallback_mode)
            self._ai_bg_fallback_mode = not prev
            try:
                self._save_toggle_state_to_file()
            except Exception as e:
                self._logger.warning(f"Failed to persist toggle state: {e}")

            self._logger.info(f"AI background fallback mode changed: {prev} -> {self._ai_bg_fallback_mode}")

            # If generation was re-enabled (toggle turned OFF), attempt an immediate background refresh in worker thread
            if not self._ai_bg_fallback_mode:
                def worker_refresh():
                    try:
                        self._ai_bg.refresh_background_if_needed()
                    except Exception as e:
                        self._logger.warning(f"Background refresh after toggle failed: {e}")

                threading.Thread(target=worker_refresh, daemon=True).start()

            # Immediately update screensaver to reflect the new mode (if screensaver active or eligible)
            if (
                self._state_manager.get_state().current == DisplayState.SCREENSAVER
                or self._state_manager.no_music_detected_for_more_than_a_minute()
            ):
                weather_info = self._weather_service.get_weather_info()
                # Decide fallback path and dot visibility
                fallback_path = None
                show_dot = False
                try:
                    if self._ai_bg_fallback_mode:
                        fallback_path = self._ai_bg.get_fallback_path()
                        if fallback_path:
                            show_dot = True
                except Exception:
                    fallback_path = None

                # Update display immediately
                self._set_screensaver_state_and_update_display(weather_info, show_ai_dot=show_dot, fallback_image_path=fallback_path)
        except Exception as e:
            self._logger.error(f"Error toggling AI background fallback mode: {e}")
            self._logger.error(traceback.format_exc())

    def _handle_button_c(self) -> None:
        """Cycle through all combinations of orientation (portrait/landscape) and rotation (true/false)."""
        try:
            # Determine current rotation state
            current_rotation = (
                (self._portrait_rotate_degrees == 270) if self._orientation == "portrait"
                else (self._landscape_rotate_degrees == 180)
            )
            
            # Cycle through states: portrait/false -> portrait/true -> landscape/false -> landscape/true -> back to start
            if self._orientation == "portrait" and not current_rotation:
                # State 1 -> 2: Portrait rotation False (90°) to True (270°)
                self._portrait_rotate_degrees = 270
                next_state = "portrait (rotated 270°)"
            elif self._orientation == "portrait" and current_rotation:
                # State 2 -> 3: Portrait rotation True to Landscape rotation False (0°)
                self._orientation = "landscape"
                self._landscape_rotate_degrees = 0
                next_state = "landscape (rotated 0°)"
            elif self._orientation == "landscape" and not current_rotation:
                # State 3 -> 4: Landscape rotation False to True (180°)
                self._landscape_rotate_degrees = 180
                next_state = "landscape (rotated 180°)"
            else:
                # State 4 -> 1: Landscape rotation True to Portrait rotation False (90°)
                self._orientation = "portrait"
                self._portrait_rotate_degrees = 90
                next_state = "portrait (rotated 90°)"

            # Update the display service with the new orientation and rotation settings
            self._display_service.set_orientation(
                self._orientation,
                portrait_rotate_degrees=self._portrait_rotate_degrees,
                landscape_rotate_degrees=self._landscape_rotate_degrees,
            )
            
            # Persist the new orientation and rotation
            try:
                self._save_toggle_state_to_file()
            except Exception as e:
                self._logger.warning(f"Failed to persist orientation change: {e}")
            
            self._logger.info(f"Display orientation/rotation changed to: {next_state}")
            
            # Redraw the current display with the new orientation
            current_state = self._state_manager.get_state().current
            if current_state == DisplayState.PLAYING:
                # Redraw the playing screen
                playing_state = self._state_manager.get_playing_state()
                # Create minimal SongInfo from stored state
                from service.song_identify_service import SongInfo
                song_info = SongInfo(
                    title=playing_state.song_title,
                    artist=playing_state.song_artist,
                    album=None,
                    release_year=None,
                    album_art=None
                )
                self._display_service.update_display_to_playing(song_info)
            elif current_state == DisplayState.SCREENSAVER:
                # Redraw the screensaver with current weather
                weather_info = self._weather_service.get_weather_info()
                fallback_path = None
                show_dot = False
                try:
                    if self._ai_bg_fallback_mode:
                        fallback_path = self._ai_bg.get_fallback_path()
                        if fallback_path:
                            show_dot = True
                except Exception:
                    fallback_path = None
                self._display_service.update_display_to_screensaver(weather_info, show_ai_dot=show_dot, fallback_image_path=fallback_path)
        except Exception as e:
            self._logger.error(f"Error toggling orientation: {e}")
            self._logger.error(traceback.format_exc())

    def _state_file_path(self) -> str:
        # Persist toggle state next to the main config YAML
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config'))
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        return os.path.join(base, 'toggle_state.json')

    def _ensure_toggle_state_file_exists(self) -> None:
        """Create toggle_state.json with default values if it doesn't exist."""
        path = self._state_file_path()
        try:
            if not os.path.exists(path):
                self._logger.info(f"Creating toggle state file with defaults: {path}")
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(
                        {
                            'ai_bg_fallback_mode': True,
                            'orientation': 'portrait',
                            'rotation': False,
                        },
                        f,
                        indent=4,
                    )
        except Exception as e:
            self._logger.warning(f"Failed to create default toggle state file: {e}")

    def _get_toggle_state_mtime(self) -> Optional[float]:
        try:
            return os.path.getmtime(self._state_file_path())
        except OSError:
            return None

    def _refresh_toggle_state_if_changed(self) -> None:
        """Reload toggle_state.json if it changed since last load/save."""
        try:
            current_mtime = self._get_toggle_state_mtime()
            if current_mtime is not None and current_mtime != self._toggle_state_mtime:
                self._logger.info("Toggle state file changed; reloading")
                self._load_toggle_state_from_file()
                self._toggle_state_mtime = current_mtime
        except Exception as e:
            self._logger.warning(f"Toggle state refresh error: {e}")

    def _load_toggle_state_from_file(self) -> None:
        path = self._state_file_path()
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._ai_bg_fallback_mode = bool(data.get('ai_bg_fallback_mode', False))
                    self._orientation = (data.get('orientation') or self._orientation).lower()

                    # Consolidated rotation structure: a single bool or legacy per-orientation values.
                    # Booleans: portrait True/False -> 90/270; landscape True/False -> 0/180.
                    rotation_raw = data.get('rotation')

                    def _decode_rotation_value(raw_value: object, orientation: str, default_degrees: int) -> int:
                        if isinstance(raw_value, bool):
                            return 270 if (raw_value and orientation == "portrait") else (
                                90 if orientation == "portrait" else (180 if raw_value else 0)
                            )
                        try:
                            return int(raw_value)
                        except Exception:
                            return default_degrees

                    if isinstance(rotation_raw, bool):
                        # Single bool drives both orientations
                        self._portrait_rotate_degrees = _decode_rotation_value(rotation_raw, 'portrait', self._portrait_rotate_degrees)
                        self._landscape_rotate_degrees = _decode_rotation_value(rotation_raw, 'landscape', self._landscape_rotate_degrees)
                    else:
                        portrait_raw = rotation_raw.get('portrait') if isinstance(rotation_raw, dict) else rotation_raw
                        landscape_raw = rotation_raw.get('landscape') if isinstance(rotation_raw, dict) else rotation_raw

                        self._portrait_rotate_degrees = _decode_rotation_value(
                            portrait_raw if portrait_raw is not None else data.get('portrait_rotate_degrees'),
                            'portrait',
                            self._portrait_rotate_degrees,
                        )
                        self._landscape_rotate_degrees = _decode_rotation_value(
                            landscape_raw if landscape_raw is not None else data.get('landscape_rotate_degrees'),
                            'landscape',
                            self._landscape_rotate_degrees,
                        )

                    self._display_service.set_orientation(
                        self._orientation,
                        portrait_rotate_degrees=self._portrait_rotate_degrees,
                        landscape_rotate_degrees=self._landscape_rotate_degrees,
                    )
                    self._logger.info(f"Loaded AI background fallback mode from {path}: {self._ai_bg_fallback_mode}")
                    self._logger.info(
                        "Loaded display orientation=%s, portrait_rotate=%s, landscape_rotate=%s",
                        self._orientation,
                        self._portrait_rotate_degrees,
                        self._landscape_rotate_degrees,
                    )
        except Exception as e:
            self._logger.warning(f"Failed to load toggle state from {path}: {e}")

    def _save_toggle_state_to_file(self) -> None:
        path = self._state_file_path()
        try:
            temp = path + '.tmp'
            with open(temp, 'w', encoding='utf-8') as f:
                def _encode_rotation_bool(p_deg: int, l_deg: int) -> bool:
                    p_bool = True if p_deg == 270 else False if p_deg == 0 else None
                    l_bool = True if l_deg == 180 else False if l_deg == 0 else None
                    if p_bool is not None and l_bool is not None and p_bool == l_bool:
                        return p_bool
                    if p_bool is not None:
                        return p_bool
                    if l_bool is not None:
                        return l_bool
                    return True  # fallback

                json.dump(
                    {
                        'ai_bg_fallback_mode': bool(self._ai_bg_fallback_mode),
                        'orientation': self._orientation,
                        'rotation': _encode_rotation_bool(self._portrait_rotate_degrees, self._landscape_rotate_degrees),
                    },
                    f,
                )
            try:
                os.replace(temp, path)
            except Exception:
                # os.replace may not be atomic on some platforms; fallback to rename
                os.rename(temp, path)
            self._toggle_state_mtime = self._get_toggle_state_mtime()
            self._logger.debug(f"Persisted AI background fallback mode to {path}: {self._ai_bg_fallback_mode}")
        except Exception as e:
            self._logger.warning(f"Failed to persist toggle state to {path}: {e}")


if __name__ == "__main__":
    service = NowPlaying()
    service.run()