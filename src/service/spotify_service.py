import spotipy
from spotipy.oauth2 import SpotifyOAuth
from typing import Optional, Tuple
import logging
import re

import sys
sys.path.append("..")
from logger import Logger
from config import Config
from settings_store import SettingsStore, SpotifyDbCacheHandler


class SpotifyService:
    def __init__(self):
        self._logger: logging.Logger = Logger().get_logger()
        self._config: dict = Config().get_config()
        orchestrator_cfg = self._config.get("orchestrator", {}) if isinstance(self._config.get("orchestrator", {}), dict) else {}
        self._cache_ttl_seconds = max(0, int(orchestrator_cfg.get("cache_ttl_seconds", 86400)))
        self._cache_size = max(0, int(orchestrator_cfg.get("cache_size", 512)))
        self._cache_enabled = self._cache_ttl_seconds > 0 and self._cache_size > 0
        self._settings_store = SettingsStore()
        self._spotify_cache_handler = SpotifyDbCacheHandler(self._settings_store)
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=self._config['spotify']['client_id'],
            client_secret=self._config['spotify']['client_secret'],
            redirect_uri="http://127.0.0.1:8888/callback",
            scope="",
            open_browser=False
            , cache_handler=self._spotify_cache_handler
        ))

    def _build_cache_key(self, title: str, artist: str) -> Optional[str]:
        normalized_title = re.sub(r"\s+", " ", (title or "").strip()).lower()
        normalized_artist = re.sub(r"\s+", " ", (artist or "").strip()).lower()
        if not normalized_title or not normalized_artist:
            return None
        return f"spotify:album_title_year:{normalized_title}|{normalized_artist}"

    def get_album_title_and_year(self, title: str, artist: str) -> Tuple[Optional[str], Optional[str]]:
        cache_key = self._build_cache_key(title, artist)
        if self._cache_enabled and cache_key:
            cached = self._settings_store.load_enrichment_cache_entry(cache_key, self._cache_ttl_seconds)
            if cached is not None:
                return (cached.get("album_title"), cached.get("release_year"))

        query = f"track:{title} artist:{artist}"
        try:
            results = self.sp.search(q=query, type="track", limit=1) or {}
            tracks = results.get('tracks') or {}
            items = tracks.get('items', [])
            if not items:
                if self._cache_enabled and cache_key:
                    self._settings_store.save_enrichment_cache_entry(
                        cache_key,
                        {"album_title": None, "release_year": None},
                        self._cache_size,
                    )
                return (None, None)
            album = items[0].get('album', {}) or {}
            album_title = album.get('name')
            release_date = album.get('release_date')
            release_year = release_date.split('-')[0] if release_date else None
            result = (album_title, release_year)
            if self._cache_enabled and cache_key:
                self._settings_store.save_enrichment_cache_entry(
                    cache_key,
                    {"album_title": album_title, "release_year": release_year},
                    self._cache_size,
                )
            return result
        except Exception as e:
            self._logger.error(f"Error fetching album title/year from Spotify: {e}")
            return (None, None)
           
