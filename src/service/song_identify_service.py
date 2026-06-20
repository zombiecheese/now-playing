
import asyncio
import logging
from typing import Optional, Dict
import io
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from shazamio import Shazam
from dataclasses import dataclass

from logger import Logger


@dataclass(frozen=True)
class SongInfo:
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    album_art: Optional[str]
    release_year: Optional[str] = None


class SongIdentifyService:
    def __init__(self) -> None:
        self._logger: logging.Logger = Logger().get_logger()
        self._shazam: Shazam = Shazam()
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _identify_bytes(self, audio_bytes: bytes) -> Optional["SongInfo"]:
        try:
            result = await self._shazam.recognize(audio_bytes)

            if not result or "track" not in result:
                self._logger.info("No song identified in the provided audio buffer.")
                return None

            self._logger.info("Song identified in the provided audio buffer.")
            song = SongIdentifyService._parse_result(result)

            try:
                self._logger.info(
                    "Identified song: Title=%s; Artist=%s; Album=%s; Year=%s; AlbumArt=%s",
                    getattr(song, "title", "") or "",
                    getattr(song, "artist", "") or "",
                    getattr(song, "album", "") or "",
                    getattr(song, "release_year", "") or "",
                    getattr(song, "album_art", "") or "",
                )
            except Exception:
                self._logger.exception("Failed to log detailed song information.")

            return song
        except Exception as ex:
            self._logger.error("Error identifying song: %s", ex, exc_info=True)
            return None

    async def identify(self, audio_wav_buffer: io.BytesIO) -> Optional["SongInfo"]:
        try:
            audio_wav_buffer.seek(0)
            audio_bytes = audio_wav_buffer.read()
            return await self._identify_bytes(audio_bytes)

        except Exception as ex:
            self._logger.error("Error identifying song: %s", ex, exc_info=True)
            return None

    def identify_sync(self, audio_wav_buffer: io.BytesIO) -> Optional["SongInfo"]:
        """Synchronous wrapper for compatibility with existing callers.

        Uses a persistent background event loop to avoid per-call loop startup cost.
        """
        try:
            audio_wav_buffer.seek(0)
            audio_bytes = audio_wav_buffer.read()

            if not self._loop.is_running():
                return asyncio.run(self._identify_bytes(audio_bytes))

            future = asyncio.run_coroutine_threadsafe(self._identify_bytes(audio_bytes), self._loop)
            return future.result(timeout=25)
        except FutureTimeoutError:
            self._logger.error("Song identify timed out.", exc_info=True)
            return None
        except Exception as ex:
            self._logger.error("Error identifying song (sync wrapper): %s", ex, exc_info=True)
            return None


    @staticmethod
    def _parse_result(result: Optional[Dict]) -> SongInfo:
        track = result['track']
        return SongInfo(
            title=track.get('title', None),
            artist=track.get('subtitle', None),
            album=SongIdentifyService._extract_album_name(track),
            album_art=track.get('images', {}).get('coverart', None),
            release_year=SongIdentifyService._extract_release_year(track)
        )

    @staticmethod
    def _extract_album_name(track: Dict) -> Optional[str]:
        metadata = track.get('sections', [{}])[0].get('metadata', [])
        for item in metadata:
            if item.get('title') == 'Album':
                return item.get('text', None)
        return None

    @staticmethod
    def _extract_release_year(track: Dict) -> Optional[str]:
        metadata = track.get('sections', [{}])[0].get('metadata', [])
        for item in metadata:
            if item.get('title') in ('Released', 'Release Date'):
                text = (item.get('text') or "").strip()
                parts = text.split('-')
                year = parts[0].strip() if parts else None
                if year and year.isdigit() and len(year) == 4:
                    return year
                tokens = text.replace(',', ' ').split()
                for tok in reversed(tokens):
                    if tok.isdigit() and len(tok) == 4:
                        return tok
        return None

