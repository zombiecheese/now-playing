
import logging
import sys
import os
import json
import numpy as np
import traceback
import signal
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
        # Load persisted toggle state (if present)
        try:
            self._load_toggle_state_from_file()
        except Exception:
            pass

        import inspect

        # Confirm the method exists on the instance
        self._logger.debug(
            "Has refresh_background_if_needed: %s",
            hasattr(self._ai_bg, "refresh_background_if_needed")
        )

        # Optional: list public attributes for a quick eyeball check
        self._logger.debug(
            "Public attrs: %s",
            [a for a in dir(self._ai_bg) if not a.startswith("_")]
        )

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
        return self._song_identify_service.identify(wav_audio)

    def _set_playing_state_and_update_display(self, song_info: SongInfo) -> None:
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

    def _state_file_path(self) -> str:
        # Persist toggle state next to the main config YAML
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config'))
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        return os.path.join(base, 'toggle_state.json')

    def _load_toggle_state_from_file(self) -> None:
        path = self._state_file_path()
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._ai_bg_fallback_mode = bool(data.get('ai_bg_fallback_mode', False))
                    self._logger.info(f"Loaded AI background fallback mode from {path}: {self._ai_bg_fallback_mode}")
        except Exception as e:
            self._logger.warning(f"Failed to load toggle state from {path}: {e}")

    def _save_toggle_state_to_file(self) -> None:
        path = self._state_file_path()
        try:
            temp = path + '.tmp'
            with open(temp, 'w', encoding='utf-8') as f:
                json.dump({'ai_bg_fallback_mode': bool(self._ai_bg_fallback_mode)}, f)
            try:
                os.replace(temp, path)
            except Exception:
                # os.replace may not be atomic on some platforms; fallback to rename
                os.rename(temp, path)
            self._logger.debug(f"Persisted AI background fallback mode to {path}: {self._ai_bg_fallback_mode}")
        except Exception as e:
            self._logger.warning(f"Failed to persist toggle state to {path}: {e}")


if __name__ == "__main__":
    service = NowPlaying()
    service.run()