
# src/services/artist_image_service.py

import os
import io
import hashlib
import logging
import requests
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError
from shazamio import Shazam, Serialize
from shazamio.schemas.artists import ArtistQuery
from shazamio.schemas.enums import ArtistView, ArtistExtend

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    # A sane UA dramatically reduces 403s from CDNs
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Accept common image types
    "Accept": "image/avif,image/webp,image/apng,image/*;q=0.8,*/*;q=0.5",
}

class ArtistImageService:
    def __init__(self, resources_root: str, spotify_client_id: Optional[str] = None, spotify_client_secret: Optional[str] = None):
        self.cache_dir = os.path.join(resources_root, "cache", "artists")
        os.makedirs(self.cache_dir, exist_ok=True)

        self.spotify = None
        if spotify_client_id and spotify_client_secret:
            self.spotify = spotipy.Spotify(
                auth_manager=SpotifyClientCredentials(
                    client_id=spotify_client_id,
                    client_secret=spotify_client_secret
                )
            )

    async def get_image(self, *, artist_id: Optional[int], artist_name: Optional[str]) -> Optional[Image.Image]:
        # 1) Try ShazamIO editorial artwork
        if artist_id:
            url = await self._get_shazam_artist_image_url(artist_id)
            img = await self._download_and_cache(url, key=f"shazam:{artist_id}") if url else None
            if img:
                return img

        # 2) Fallback: Spotify artist images (by name)
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
                query=ArtistQuery(
                    views=[ArtistView.TOP_SONGS],
                    extend=[ArtistExtend.EDITORIAL_ARTWORK]
                )
            )
            data = Serialize.artist_v2(about)
            attrs = data.data[0].attributes
            if hasattr(attrs, "editorial_artwork") and getattr(attrs, "editorial_artwork"):
                ea = attrs.editorial_artwork
                for key in ("url", "bgImage", "artistHeroImage"):
                    if hasattr(ea, key):
                        val = getattr(ea, key)
                        if val:
                            return val
        except Exception as e:
            logger.debug("Shazam artist artwork lookup failed: %s", e, exc_info=True)
        return None

    def _get_spotify_artist_image_url(self, artist_name: str) -> Optional[str]:
        try:
            results = self.spotify.search(q=f'artist:"{artist_name}"', type="artist", limit=1)
            artists = results.get("artists", {}).get("items", [])
            if not artists:
                return None
            images = artists[0].get("images", [])
            if not images:
                return None
            return images[0].get("url")
        except Exception as e:
            logger.debug("Spotify artist search failed: %s", e, exc_info=True)
            return None

    async def _download_and_cache(self, url: Optional[str], key: str) -> Optional[Image.Image]:
        if not url:
            return None

        digest = hashlib.sha256(f"{key}|{url}".encode("utf-8")).hexdigest()
        path = os.path.join(self.cache_dir, f"{digest}.png")

        # Try cache
        if os.path.exists(path):
            try:
                with Image.open(path) as img:
                    img = ImageOps.exif_transpose(img.convert("RGBA"))
                return img
            except Exception as e:
                logger.debug("Cache read failed for %s: %s; will re-download", path, e)

        # Download with headers and content-type validation
        try:
            resp = requests.get(url, headers=_DEFAULT_HEADERS, timeout=10)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                logger.debug("URL %s returned non-image content-type: %s", url, content_type)
                return None

            buf = io.BytesIO(resp.content)
            try:
                with Image.open(buf) as img:
                    # If Pillow supports WebP/AVIF, this will work; otherwise UnidentifiedImageError triggers.
                    img = ImageOps.exif_transpose(img.convert("RGBA"))
                img.save(path, format="PNG")
                return img
            except UnidentifiedImageError as e:
                logger.debug("Pillow cannot identify image from %s: %s", url, e, exc_info=True)
                return None
        except requests.HTTPError as e:
            logger.debug("HTTP error downloading %s: %s (status=%s, ct=%s)",
                         url, e, getattr(e.response, "status_code", None),
                         e.response.headers.get("Content-Type") if getattr(e, "response", None) else None)
            return None
        except Exception as e:
            logger.debug("Unexpected error downloading %s: %s", url, e, exc_info=True)
            return None
