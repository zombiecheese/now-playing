import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional legacy import support
    yaml = None

from singleton_meta import SingletonMeta


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "config" / "now_playing.db"
LEGACY_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
LEGACY_TOGGLE_STATE_PATH = PROJECT_ROOT / "config" / "toggle_state.json"
WEATHER_CACHE_DOCUMENT = "weather_cache"
DEFAULT_BACKUP_MIN_INTERVAL_SECONDS = 300
DEFAULT_BACKUP_RETENTION_COUNT = 48


DEFAULT_CONFIG: Dict[str, Any] = {
    "display": {
        "width": 800,
        "height": 480,
        "font_path": "resources/CircularStd-Bold.otf",
        "font_fallback_path": "resources/NotoSansCJK-Regular.ttc",
        "font_size_title": 45,
        "font_size_subtitle": 30,
        "text_offset_left_px_landscape": 0,
        "text_offset_right_px_landscape": 0,
        "text_offset_top_px_landscape": 0,
        "text_offset_bottom_px_landscape": 0,
        "text_offset_text_shadow_px_landscape": 4,
        "text_offset_left_px_portrait": 5,
        "text_offset_right_px_portrait": 20,
        "text_offset_top_px_portrait": 0,
        "text_offset_bottom_px_portrait": 80,
        "text_offset_text_shadow_px_portrait": 4,
        "album_offset_left_px_landscape": 0,
        "album_offset_right_px_landscape": 0,
        "album_offset_top_px_landscape": 0,
        "album_offset_bottom_px_landscape": 0,
        "album_offset_left_px_portrait": 0,
        "album_offset_right_px_portrait": 14,
        "album_offset_top_px_portrait": 49,
        "album_offset_bottom_px_portrait": 0,
        "backdrop_blur_radius": 12,
        "backdrop_darken_alpha": 120,
        "backdrop_use_gradient": False,
        "small_album_cover_px": 450,
        "weather_background_image": "resources/ai_screensaver.png",
        "portrait_album_background_color": "black",
        "text_alignment_portrait": "center",
        "text_alignment_landscape": "left",
        "text_wrap_break_long_words": True,
        "text_wrap_hyphenate": False,
        "text_line_spacing_px": 4,
        "ai_dot_margin_x_px": 55,
        "ai_dot_margin_y_px": 45,
    },
    "weather": {
        "openweathermap_api_key": "",
        "geo_coordinates": "",
        "background_refresh_seconds": 3600,
        "timezone": "Australia/Melbourne",
    },
    "orchestrator": {
        "debounce_seconds": 30,
        "cache_ttl_seconds": 86400,
        "cache_size": 512,
    },
    "log": {
        "log_file_path": "log/now_playing.log",
    },
    "web_interface": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8088,
        "preview_image_path": "config/current_display_preview.png",
        "admin_token": "",
    },
    "openai": {
        "api_key": "",
        "provider": "openai",
        "pixazo_model": "flux-schnell",
        "pixazo_api_key": "",
        "prompt_style": "80s anime",
        "model": "gpt-image-1-mini",
    },
    "image": {
        "orientation_strategy": "cover",
        "max_square_size": 1024,
        "fallback_image_path_day_portrait": "resources/portrait_default_day.png",
        "fallback_image_path_night_portrait": "resources/portrait_default_night.png",
        "fallback_image_path_day_landscape": "resources/landscape_default_day.png",
        "fallback_image_path_night_landscape": "resources/landscape_default_night.png",
        "fallback_image_path": "resources/default.jpg",
    },
    "lighting": {
        "day": "Use daytime lighting: natural brightness, appropriate color temperature, balanced contrast, and realistic shadows.",
        "twilight": "Use twilight lighting: soft low-angle light, gentle shadows, a sky gradient, moderate contrast, and selective artificial lights beginning to appear.",
        "night": "Render with low-light exposure: markedly darker scene, high contrast, cooler ambient tones, visible artificial lighting (street lamps, train interiors/headlights, illuminated windows), reduced sky luminance.",
    },
    "audio": {
        "recording_duration_seconds": 5,
        "debugaudio": False,
        "debugaudio_path": "debug_audio",
        "gain_db": 10.0,
    },
}

DEFAULT_TOGGLE_STATE: Dict[str, Any] = {
    "ai_bg_fallback_mode": False,
    "music_detection_enabled": True,
    "orientation": "portrait",
    "rotation": False,
}


class SettingsStore(metaclass=SingletonMeta):
    def __init__(self) -> None:
        self._database_path = DATABASE_PATH
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    name TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS enrichment_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    def _deep_copy(self, value: Dict[str, Any]) -> Dict[str, Any]:
        return json.loads(json.dumps(value))

    def _read_document_row(self, name: str) -> Optional[sqlite3.Row]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, updated_at FROM documents WHERE name = ?",
                (name,),
            ).fetchone()
            return row

    def _write_document(self, name: str, document: Dict[str, Any]) -> None:
        payload = json.dumps(document, ensure_ascii=False)
        updated_at = self._utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO documents (name, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (name, payload, updated_at),
            )
            connection.commit()

    def _load_legacy_yaml(self) -> Optional[Dict[str, Any]]:
        if not LEGACY_CONFIG_PATH.exists() or yaml is None:
            return None
        try:
            with LEGACY_CONFIG_PATH.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _load_legacy_json(self) -> Optional[Dict[str, Any]]:
        if not LEGACY_TOGGLE_STATE_PATH.exists():
            return None
        try:
            with LEGACY_TOGGLE_STATE_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle) or {}
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _load_document(self, name: str, default_document: Dict[str, Any]) -> Dict[str, Any]:
        row = self._read_document_row(name)
        if row is not None:
            try:
                payload = json.loads(row["payload"])
                return payload if isinstance(payload, dict) else self._deep_copy(default_document)
            except Exception:
                return self._deep_copy(default_document)

        if name == "config":
            legacy = self._load_legacy_yaml()
            if legacy is not None:
                self._write_document(name, legacy)
                return self._deep_copy(legacy)
        elif name == "toggle_state":
            legacy = self._load_legacy_json()
            if legacy is not None:
                self._write_document(name, legacy)
                return self._deep_copy(legacy)

        self._write_document(name, default_document)
        return self._deep_copy(default_document)

    def load_config(self) -> Dict[str, Any]:
        return self._load_document("config", DEFAULT_CONFIG)

    def save_config(self, config: Dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise ValueError("Config payload must be a dictionary.")
        self._write_document("config", config)

    def load_toggle_state(self) -> Dict[str, Any]:
        return self._load_document("toggle_state", DEFAULT_TOGGLE_STATE)

    def save_toggle_state(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(updates, dict):
            raise ValueError("Toggle state updates must be a dictionary.")
        current = self.load_toggle_state()
        current.update(updates)
        self._write_document("toggle_state", current)
        return self._deep_copy(current)

    def toggle_state_version(self) -> Optional[str]:
        row = self._read_document_row("toggle_state")
        if row is None:
            return None
        return str(row["updated_at"])

    def config_version(self) -> Optional[str]:
        row = self._read_document_row("config")
        if row is None:
            return None
        return str(row["updated_at"])

    def backup_database(
        self,
        min_interval_seconds: int = DEFAULT_BACKUP_MIN_INTERVAL_SECONDS,
        retention_count: int = DEFAULT_BACKUP_RETENTION_COUNT,
    ) -> Optional[Path]:
        if not self._database_path.exists():
            return None
        backup_dir = PROJECT_ROOT / "config" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        backup_files = sorted(backup_dir.glob("now_playing_*.db"), key=lambda p: p.stat().st_mtime)

        if min_interval_seconds > 0 and backup_files:
            last_backup = backup_files[-1]
            age_seconds = datetime.now(timezone.utc).timestamp() - last_backup.stat().st_mtime
            if age_seconds < min_interval_seconds:
                return None

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"now_playing_{timestamp}.db"
        backup_path.write_bytes(self._database_path.read_bytes())

        if retention_count > 0:
            backup_files = sorted(backup_dir.glob("now_playing_*.db"), key=lambda p: p.stat().st_mtime)
            excess = len(backup_files) - retention_count
            if excess > 0:
                for stale in backup_files[:excess]:
                    try:
                        stale.unlink()
                    except Exception:
                        continue

        return backup_path

    def load_enrichment_cache_entry(self, cache_key: str, ttl_seconds: int) -> Optional[Dict[str, Any]]:
        if not cache_key or ttl_seconds <= 0:
            return None

        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, updated_at FROM enrichment_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None

            try:
                updated_at = datetime.fromisoformat(str(row["updated_at"]))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
                if age_seconds > ttl_seconds:
                    connection.execute("DELETE FROM enrichment_cache WHERE cache_key = ?", (cache_key,))
                    connection.commit()
                    return None
            except Exception:
                connection.execute("DELETE FROM enrichment_cache WHERE cache_key = ?", (cache_key,))
                connection.commit()
                return None

            try:
                payload = json.loads(row["payload"])
            except Exception:
                return None
            return payload if isinstance(payload, dict) else None

    def save_enrichment_cache_entry(self, cache_key: str, payload: Dict[str, Any], max_entries: int) -> None:
        if not cache_key or not isinstance(payload, dict) or max_entries <= 0:
            return

        payload_json = json.dumps(payload, ensure_ascii=False)
        updated_at = self._utc_now()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO enrichment_cache (cache_key, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (cache_key, payload_json, updated_at),
            )

            row = connection.execute("SELECT COUNT(*) AS count FROM enrichment_cache").fetchone()
            total_entries = int(row["count"] if row is not None else 0)
            excess_entries = total_entries - max_entries
            if excess_entries > 0:
                stale_rows = connection.execute(
                    "SELECT cache_key FROM enrichment_cache ORDER BY updated_at ASC LIMIT ?",
                    (excess_entries,),
                ).fetchall()
                for stale_row in stale_rows:
                    connection.execute(
                        "DELETE FROM enrichment_cache WHERE cache_key = ?",
                        (stale_row["cache_key"],),
                    )
            connection.commit()

    def load_weather_cache(self) -> Optional[Dict[str, Any]]:
        row = self._read_document_row(WEATHER_CACHE_DOCUMENT)
        if row is None:
            return None

        try:
            payload = json.loads(row["payload"])
        except Exception:
            return None

        return payload if isinstance(payload, dict) else None

    def save_weather_cache(self, weather_cache: Dict[str, Any]) -> None:
        if not isinstance(weather_cache, dict):
            raise ValueError("Weather cache payload must be a dictionary.")
        self._write_document(WEATHER_CACHE_DOCUMENT, weather_cache)

    def weather_cache_updated_at(self) -> Optional[str]:
        row = self._read_document_row(WEATHER_CACHE_DOCUMENT)
        if row is None:
            return None
        return str(row["updated_at"])

    def enrichment_cache_stats(self) -> Dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count, MIN(updated_at) AS oldest, MAX(updated_at) AS newest FROM enrichment_cache"
            ).fetchone()
            count = int(row["count"] if row is not None else 0)
            oldest = str(row["oldest"]) if row is not None and row["oldest"] is not None else None
            newest = str(row["newest"]) if row is not None and row["newest"] is not None else None
            return {
                "entry_count": count,
                "oldest_updated_at": oldest,
                "newest_updated_at": newest,
            }
