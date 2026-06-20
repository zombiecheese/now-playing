import io
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.signal import resample
import scipy.io.wavfile as wav
from logger import Logger
from config import Config

class AudioProcessingUtils:
    _logger: logging.Logger = Logger().get_logger()
    _project_root = Path(__file__).resolve().parent.parent
    _default_debug_dir = (_project_root / 'debug_audio').resolve()

    @staticmethod
    def _resolve_safe_debug_dir(configured_path: str) -> Path:
        configured = str(configured_path or '').strip()
        if not configured:
            return AudioProcessingUtils._default_debug_dir

        raw = Path(configured).expanduser()
        resolved = raw.resolve() if raw.is_absolute() else (AudioProcessingUtils._project_root / raw).resolve()
        try:
            resolved.relative_to(AudioProcessingUtils._project_root)
        except ValueError as exc:
            raise ValueError(f"debugaudio_path must stay within {AudioProcessingUtils._project_root}") from exc
        return resolved

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
                    dir_path = AudioProcessingUtils._resolve_safe_debug_dir(str(debug_path or ''))
                    dir_path.mkdir(parents=True, exist_ok=True)
                    filename = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
                    file_path = dir_path / filename
                    with file_path.open('wb') as f:
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