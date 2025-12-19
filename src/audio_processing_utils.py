import io
import logging
import os
from datetime import datetime

import numpy as np
from scipy.signal import resample
import scipy.io.wavfile as wav
from logger import Logger
from config import Config

class AudioProcessingUtils:
    _logger: logging.Logger = Logger().get_logger()

    @staticmethod
    def resample(audio: np.ndarray, source_sampling_rate: int, target_sampling_rate: int) -> np.ndarray:
        try:
            samples = int(len(audio) * target_sampling_rate / source_sampling_rate)
            return np.squeeze(resample(audio, samples))
        except Exception as e:
            AudioProcessingUtils._logger.error(f"Resampling failed: {e}")
            raise RuntimeError("Resampling failed.") from e

    @staticmethod
    def to_wav(audio: np.ndarray, sampling_rate: int) -> io.BytesIO:
        try:
            buffer = io.BytesIO()
            wav.write(buffer, sampling_rate, audio)
            buffer.seek(0)

            # Optionally write a debug copy to disk when enabled in config
            try:
                cfg = Config().get_config()
                audio_cfg = cfg.get('audio', {}) if isinstance(cfg, dict) else {}
                debug_enabled = bool(audio_cfg.get('debugaudio', False))
                if debug_enabled:
                    debug_path = audio_cfg.get('debugaudio_path')
                    if debug_path:
                        os.makedirs(debug_path, exist_ok=True)
                        dir_path = debug_path
                    else:
                        dir_path = os.path.join(os.getcwd(), 'debug_audio')
                        os.makedirs(dir_path, exist_ok=True)
                    filename = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
                    file_path = os.path.join(dir_path, filename)
                    with open(file_path, 'wb') as f:
                        f.write(buffer.getvalue())
                    AudioProcessingUtils._logger.info(f"Wrote debug WAV to {file_path}")
                    buffer.seek(0)
            except Exception as e:
                AudioProcessingUtils._logger.warning(f"Failed to write debug WAV to disk: {e}")

            return buffer
        except Exception as e:
            AudioProcessingUtils._logger.error(f"WAV conversion failed: {e}")
            raise RuntimeError("WAV conversion failed.") from e

    @staticmethod
    def float32_to_int16(audio: np.ndarray) -> np.ndarray:
        try:
            audio = np.clip(audio, -1.0, 1.0)  # Avoid overflow
            return np.int16(audio * 32767)
        except Exception as e:
            AudioProcessingUtils._logger.error(f"Conversion to int16 failed: {e}")
            raise RuntimeError("float32 to int16 conversion failed.") from e