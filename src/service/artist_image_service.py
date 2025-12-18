
# src/service/artist_image_service.py

import hashlib
import io
import logging
import os
from typing import Optional

import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from shazamio import Shazam, Serialize
from shazamio.schemas.artists import ArtistQuery
from shazamio.schemas.enums import ArtistView, ArtistExtend
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*;q=0.8,*/*;q=0.5",
}


class ArtistImageService:
    def __init__(self, resources_root: str, spotify_client_id: Optional[str] = None, spotify_client_secret: Optional[str] = None):
        self.cache_dir = os.path.join(resources_root, "cache", "artists")
        os.makedirs(self.cache_dir, exist_ok=True)

        self._session = requests.Session()
        self._session.headers.update(_DEFAULT_HEADERS)

        self.spotify = None
        if spotify_client_id and spotify_client_secret:
            self.spotify = spotipy.Spotify(
                auth_manager=SpotifyClientCredentials(
                    client_id=spotify_client_id, client_secret=spotify_client_secret
                )
            )

    async def get_image(self, *, artist_id: Optional[int], artist_name: Optional[str]) -> Optional[Image.Image]:
        """Return a Pillow Image or None. Tries Shazam (by id) then Spotify (by name)."""
        if artist_id:
            url = await self._get_shazam_artist_image_url(artist_id)
            img = await self._download_and_cache(url, key=f"shazam:{artist_id}") if url else None
            if img:
                return img

        if self.spotify and artist_name:
            url = self._get_spotify_artist_image_url(artist_name)
            img = await self._download_and_cache(url, key=f"spotify:{artist_name}") if url else None
            if img:
                return img

        return None

    async def _get_shazam_artist_image_url(self, artist_id: int) -> Optional[str]:
        try:
            shazam = Shazam()
            about = await shazam.artist_about(
                artist_id,
                query=ArtistQuery(views=[ArtistView.TOP_SONGS], extend=[ArtistExtend.EDITORIAL_ARTWORK]),
            )
            data = Serialize.artist_v2(about)
            attrs = getattr(data.data[0], "attributes", None)
            ea = getattr(attrs, "editorial_artwork", None)
            if not ea:
                return None
            for key in ("url", "bgImage", "artistHeroImage"):
                val = getattr(ea, key, None)
                if val:
                    return val
        except Exception:
            logger.debug("Shazam artist artwork lookup failed", exc_info=True)
        return None

    def _get_spotify_artist_image_url(self, artist_name: str) -> Optional[str]:
        try:
            results = self.spotify.search(q=f'artist:"{artist_name}"', type="artist", limit=1)
            artist = results.get("artists", {}).get("items", [])
            if not artist:
                return None
            images = artist[0].get("images", [])
            return images[0].get("url") if images else None
        except Exception:
            logger.debug("Spotify artist search failed", exc_info=True)
            return None

    async def _download_and_cache(self, url: Optional[str], key: str) -> Optional[Image.Image]:
        if not url:
            return None

        digest = hashlib.sha256(f"{key}|{url}".encode("utf-8")).hexdigest()
        path = os.path.join(self.cache_dir, f"{digest}.png")

        if os.path.exists(path):
            try:
                with Image.open(path) as img:
                    return ImageOps.exif_transpose(img.convert("RGBA"))
            except Exception:
                logger.debug("Failed to read cache %s; will re-download", path, exc_info=True)

        try:
            resp = self._session.get(url, timeout=10)
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "")
            if not ct.lower().startswith("image/"):
                logger.debug("Non-image content-type %s from %s", ct, url)
                return None

            buf = io.BytesIO(resp.content)
            try:
                with Image.open(buf) as img:
                    out = ImageOps.exif_transpose(img.convert("RGBA"))
                out.save(path, format="PNG")
                return out
            except UnidentifiedImageError:
                logger.debug("Pillow cannot identify image from %s", url, exc_info=True)
                return None
        except Exception:
            logger.debug("Error downloading image %s", url, exc_info=True)
            return None
