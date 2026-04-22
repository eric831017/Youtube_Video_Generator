"""Configuration, constants, environment loading, settings persistence."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("video_bot")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

SETTINGS_PATH = DATA_DIR / "settings.json"
COST_TRACKER_PATH = DATA_DIR / "cost_tracker.json"
TOKEN_PATH = DATA_DIR / "token.json"
CLIENT_SECRETS_PATH = DATA_DIR / "client_secrets.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USER_ID = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "0") or 0)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
FAL_API_KEY = os.getenv("FAL_API_KEY", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_DRIVE_FOLDER_NAME = os.getenv("GOOGLE_DRIVE_FOLDER_NAME", "AI Videos")

GOOGLE_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/youtube.upload",
]

MAX_IMAGE_BYTES = 20 * 1024 * 1024

MODELS: Dict[str, Dict[str, Any]] = {
    "kling-2.1": {
        "t2v_fal_id": "fal-ai/kling-video/v2.1/standard/text-to-video",
        "i2v_fal_id": "fal-ai/kling-video/v2.1/standard/image-to-video",
        "price_per_sec": 0.010,
        "label": "Kling 2.1",
        "max_duration": 10,
    },
    "seedance-1.5": {
        "t2v_fal_id": "fal-ai/seedance-video/v1.5-pro/text-to-video",
        "i2v_fal_id": "fal-ai/seedance-video/v1.5-pro/image-to-video",
        "price_per_sec": 0.014,
        "label": "Seedance 1.5",
        "max_duration": 10,
    },
    "veo-3.1": {
        "t2v_fal_id": "fal-ai/veo3",
        "i2v_fal_id": "fal-ai/veo3",
        "price_per_sec": 0.016,
        "label": "Veo 3.1",
        "max_duration": 8,
    },
    "wan-2.6": {
        "t2v_fal_id": "fal-ai/wan-video/v2.6/text-to-video",
        "i2v_fal_id": "fal-ai/wan-video/v2.6/image-to-video",
        "price_per_sec": 0.005,
        "label": "Wan 2.6",
        "max_duration": 10,
    },
}

ASPECT_RATIOS = ["16:9", "9:16", "1:1", "4:3"]
DURATIONS = [5, 8, 10]
RESOLUTIONS = ["720p", "1080p"]
YOUTUBE_PRIVACY_OPTIONS = ["public", "unlisted", "private"]
YOUTUBE_CATEGORIES = {
    "1": "Film & Animation",
    "10": "Music",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "27": "Education",
    "28": "Science & Technology",
}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "default_model": "kling-2.1",
    "default_ratio": "16:9",
    "default_duration": 8,
    "default_resolution": "1080p",
    "monthly_budget_usd": 10.00,
    "youtube_default_privacy": "public",
    "youtube_default_category": "28",
}


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_settings() -> Dict[str, Any]:
    if not SETTINGS_PATH.exists():
        _atomic_write_json(SETTINGS_PATH, DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("settings.json unreadable (%s), restoring defaults", exc)
        _atomic_write_json(SETTINGS_PATH, DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)
    changed = False
    for key, value in DEFAULT_SETTINGS.items():
        if key not in data:
            data[key] = value
            changed = True
    if changed:
        _atomic_write_json(SETTINGS_PATH, data)
    return data


def save_settings(data: Dict[str, Any]) -> None:
    _atomic_write_json(SETTINGS_PATH, data)


def update_setting(key: str, value: Any) -> Dict[str, Any]:
    settings = load_settings()
    settings[key] = value
    save_settings(settings)
    return settings


def validate_env() -> None:
    missing = []
    for name in ("TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "FAL_API_KEY"):
        if not globals().get(name):
            missing.append(name)
    if TELEGRAM_ALLOWED_USER_ID == 0:
        missing.append("TELEGRAM_ALLOWED_USER_ID")
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )
