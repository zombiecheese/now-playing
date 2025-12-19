
import asyncio
import logging
from typing import Optional, Dict
import io
from shazamio import Shazam
from dataclasses import dataclass

import sys
sys.path.append("..")
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

    def identify(self, audio_wav_buffer: io.BytesIO) -> Optional[SongInfo]:
        try:
            result = asyncio.run(self._shazam.recognize(audio_wav_buffer.read()))
            if not result or "track" not in result:
                self._logger.info("No song identified in the provided audio buffer.")
                return None
            self._logger.info("Song identified in the provided audio buffer.")
            song = SongIdentifyService._parse_result(result)
            # Log detailed identified song information
            try:
                self._logger.info(
                    "Identified song: Title=%s; Artist=%s; Album=%s; Year=%s; AlbumArt=%s",
                    song.title or "",
                    song.artist or "",
                    song.album or "",
                    song.release_year or "",
                    song.album_art or ""
                )
            except Exception:
                # Ensure logging doesn't break identification flow
                self._logger.exception("Failed to log detailed song information.")
            return song
        except Exception as ex:
            self._logger.error(f"Error identifying song: {ex}")
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
       
