
import io
import time
import json
import dataclasses
from collections import OrderedDict
from typing import Optional, Tuple

import sys
sys.path.append("..")
from logger import Logger
from config import Config
from service.song_identify_service import SongIdentifyService, SongInfo
from service.spotify_service import SpotifyService
from service.display_service import DisplayService


class NowPlayingOrchestrator:
    def __init__(self) -> None:
        self._logger = Logger().get_logger()
        self._config = Config().get_config()

        self._identify = SongIdentifyService()
        self._spotify = SpotifyService()
        self._display = DisplayService()

        oconf = self._config.get("orchestrator", {})
        self._debounce_seconds = int(oconf.get("debounce_seconds", 30))
        self._cache_ttl_seconds = int(oconf.get("cache_ttl_seconds", 86400))
        self._cache_size = int(oconf.get("cache_size", 512))
        self._cache_file_path = oconf.get("cache_file_path", "")

        self._enrichment_cache: OrderedDict[str, Tuple[str, str, float]] = OrderedDict()
        self._last_track_key: Optional[str] = None
        self._last_update_ts: float = 0.0

        self._load_cache_from_disk()

    def process(self, audio_wav_buffer: io.BytesIO, force_update: bool = False) -> Optional[SongInfo]:
        # Use synchronous wrapper to invoke async identify
        song_info = self._identify.identify_sync(audio_wav_buffer)
        if not song_info:
            return None

        key = self._make_key(song_info.title, song_info.artist)

        album_title, release_year = self._get_cached_or_fetch_album_year(song_info.title or "", song_info.artist or "")
        song_info = dataclasses.replace(
            song_info,
            album=song_info.album or album_title,
            release_year=song_info.release_year or release_year
        )

        if not force_update and self._is_debounced(key):
            return song_info

        self._display.update_display_to_playing(song_info)
        self._last_track_key = key
        self._last_update_ts = time.time()
        return song_info

    def _make_key(self, title: Optional[str], artist: Optional[str]) -> str:
        t = (title or "").strip().lower()
        a = (artist or "").strip().lower()
        return f"{t}|{a}"

    def _is_debounced(self, key: str) -> bool:
        if self._last_track_key != key:
            return False
        return (time.time() - self._last_update_ts) < self._debounce_seconds

    def _get_cached_or_fetch_album_year(self, title: str, artist: str) -> Tuple[Optional[str], Optional[str]]:
        key = self._make_key(title, artist)
        now = time.time()

        if key in self._enrichment_cache:
            album, year, ts = self._enrichment_cache[key]
            if (now - ts) < self._cache_ttl_seconds:
                self._enrichment_cache.move_to_end(key)
                return (album or None, year or None)
            else:
                self._enrichment_cache.pop(key, None)

        album_title, release_year = self._spotify.get_album_title_and_year(title, artist)
        self._put_cache(key, album_title or "", release_year or "")
        self._persist_cache_to_disk()
        return (album_title, release_year)

    def _put_cache(self, key: str, album: str, year: str) -> None:
        self._enrichment_cache[key] = (album, year, time.time())
        self._enrichment_cache.move_to_end(key)
        while len(self._enrichment_cache) > self._cache_size:
            self._enrichment_cache.popitem(last=False)

    def _load_cache_from_disk(self) -> None:
        if not self._cache_file_path:
            return
        try:
            with open(self._cache_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                album = v.get("album", "")
                year = v.get("year", "")
                ts = float(v.get("ts", 0.0))
                self._enrichment_cache[k] = (album, year, ts)
            self._logger.info(f"Loaded {len(self._enrichment_cache)} cache entries from disk.")
        except Exception:
            pass

    def _persist_cache_to_disk(self) -> None:
        if not self._cache_file_path:
            return
        try:
            data = {k: {"album": v[0], "year": v[1], "ts": v[2]} for k, v in self._enrichment_cache.items()}
            with open(self._cache_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            # Log the failure but don't raise; persistence is non-fatal
            try:
                self._logger.exception("Failed to persist enrichment cache to disk.")
            except Exception:
                # Fallback: avoid raising from logger failures
                pass
           
