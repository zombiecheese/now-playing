import logging

import sounddevice as sd
import numpy as np
from config import Config
from typing import Optional, Tuple

import sys
sys.path.append("..")
from logger import Logger


class AudioRecordingService:
    def __init__(self, sampling_rate: int, channels: int) -> None:
        self._logger: logging.Logger = Logger().get_logger()
        self._sampling_rate: int = sampling_rate
        self._channels: int = channels
        self._setup_device()

    def _setup_device(self) -> None:
        try:
            sd.default.samplerate = self._sampling_rate
            sd.default.channels = self._channels
            device_information = self._get_device_information()
            if device_information:
                device_index, device_name = device_information
                sd.default.device = (device_index, None)
                self._logger.debug(f"Using audio device: {device_name}")
            else:
                self._logger.error("No audio device found.")
        except Exception as e:
            self._logger.error(f"Audio device setup failed: {e}")
            raise RuntimeError("Audio device setup failed.") from e

    def _get_device_information(self) -> Optional[Tuple[int, str]]:
        try:
            devices = sd.query_devices()
            for idx, device in enumerate(devices):
                if 'usb' in device['name'].lower():
                    return idx, device['name']
            return None
        except Exception as e:
            self._logger.error(f"Device query failed: {e}")
            return None

    def record(self, duration: float) -> np.ndarray:
        if duration <= 0:
            raise ValueError("Duration must be positive.")

        try:
            self._logger.info(f"Recording for {duration} seconds at {self._sampling_rate} Hz.")
            audio = sd.rec(int(duration * self._sampling_rate), dtype=np.float32)
            sd.wait()
            audio = np.squeeze(audio)

            # Apply optional gain (in dB) from config
            try:
                cfg = Config().get_config()
                audio_cfg = cfg.get('audio', {}) if isinstance(cfg, dict) else {}
                gain_db = float(audio_cfg.get('gain_db', 0.0) or 0.0)
                if gain_db != 0.0:
                    multiplier = 10 ** (gain_db / 20.0)
                    self._logger.debug(f"Applying gain {gain_db} dB -> multiplier {multiplier:.3f}")
                    audio = audio * multiplier
                    # Clip to float32 audio range to avoid overflow
                    audio = np.clip(audio, -1.0, 1.0)
            except Exception as e:
                self._logger.warning(f"Failed to apply gain from config: {e}")

            return audio
        except Exception as e:
            self._logger.error(f"Recording failed: {e}")
            raise RuntimeError("Recording failed.") from e
