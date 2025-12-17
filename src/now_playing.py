
import logging
import sys
import numpy as np
import traceback
import signal
from typing import Tuple, Final
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

        import inspect

        # Show the class and where it was loaded from
        self._logger.debug("AIBackgroundService type: %s", type(self._ai_bg))
        self._logger.debug("AIBackgroundService module: %s", self._ai_bg.__class__.__module__)
        self._logger.debug("AIBackgroundService file: %s", inspect.getfile(self._ai_bg.__class__))

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
        # (1) Try generating a fresh AI background image (if needed & API key configured).
        try:
            self._ai_bg.refresh_background_if_needed()
        except Exception as e:
            # Non-fatal: we still proceed with weather rendering
            self._logger.warning(f"AI background refresh skipped: {e}")

        # (2) Decide whether to (re)render screensaver with up-to-date weather info
        if (
            self._state_manager.get_state().current != DisplayState.SCREENSAVER
            and self._state_manager.no_music_detected_for_more_than_a_minute()
        ) or self._state_manager.screensaver_still_up_but_weather_info_outdated():
            weather_info = self._weather_service.get_weather_info()
            self._set_screensaver_state_and_update_display(weather_info)

    def _set_screensaver_state_and_update_display(self, weather_info: WeatherInfo) -> None:
        if self._state_manager.should_clean_display():
            self._clean_display_and_set_clean_state()
        self._state_manager.set_screensaver_state(weather_info)
        self._display_service.update_display_to_screensaver(weather_info)
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


if __name__ == "__main__":
    service = NowPlaying()
    service.run()