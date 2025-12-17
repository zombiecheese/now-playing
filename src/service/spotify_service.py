
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from typing import Optional, Tuple
import logging

import sys
sys.path.append("..")
from logger import Logger
from config import Config


class SpotifyService:
    def __init__(self):
        self._logger: logging.Logger = Logger().get_logger()
        self._config: dict = Config().get_config()
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=self._config['spotify']['client_id'],
            client_secret=self._config['spotify']['client_secret'],
            redirect_uri="http://127.0.0.1:8888/callback",
            scope="playlist-modify-public playlist-modify-private",
            open_browser=False
        ))

    def search_track_uri(self, title: str, artist: str) -> Optional[str]:
        query = f"track:{title} artist:{artist}"
        self._logger.debug(f"Searching for track with query: {query}")
        try:
            results = self.sp.search(q=query, type="track", limit=1)
            tracks = results.get('tracks', {}).get('items', [])
            if tracks:
                track_uri = tracks[0]['uri']
                self._logger.info(f"Found track URI: {track_uri}")
                return track_uri
            self._logger.warning(f"No track found for '{title}' by '{artist}'.")
            return None
        except Exception as e:
            self._logger.error(f"Error searching for track '{title}' by '{artist}': {e}")
            return None

    def add_to_playlist(self, track_uri: str) -> None:
        try:
            playlist_id = self._config['spotify']['playlist_id']
            self.sp.playlist_add_items(playlist_id, [track_uri])
            self._logger.info(f"Successfully added track '{track_uri}' to playlist '{playlist_id}'.")
        except Exception as e:
            self._logger.error(f"Failed to add track '{track_uri}' to playlist: {e}.")

    def get_album_title_and_year(self, title: str, artist: str) -> Tuple[Optional[str], Optional[str]]:
        query = f"track:{title} artist:{artist}"
        try:
            results = self.sp.search(q=query, type="track", limit=1)
            items = results.get('tracks', {}).get('items', [])
            if not items:
                return (None, None)
            album = items[0].get('album', {}) or {}
            album_title = album.get('name')
            release_date = album.get('release_date')
            release_year = release_date.split('-')[0] if release_date else None
            return (album_title, release_year)
        except Exception as e:
            self._logger.error(f"Error fetching album title/year from Spotify: {e}")
           
