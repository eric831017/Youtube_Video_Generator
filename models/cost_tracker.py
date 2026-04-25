"""Monthly cost tracking with JSON persistence."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import COST_TRACKER_PATH, _atomic_write_json, logger


def _current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def _load_raw() -> Dict[str, Any]:
    if not COST_TRACKER_PATH.exists():
        _atomic_write_json(COST_TRACKER_PATH, {})
        return {}
    try:
        return json.loads(COST_TRACKER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("cost_tracker.json unreadable (%s), resetting", exc)
        _atomic_write_json(COST_TRACKER_PATH, {})
        return {}


def _ensure_month(data: Dict[str, Any], month: str) -> Dict[str, Any]:
    if month not in data:
        data[month] = {"spent": 0.0, "generations": 0, "log": []}
    return data[month]


def get_monthly_spent(month: Optional[str] = None) -> float:
    month = month or _current_month()
    data = _load_raw()
    bucket = data.get(month)
    if not bucket:
        return 0.0
    return float(bucket.get("spent", 0.0))


def get_monthly_summary(month: Optional[str] = None) -> Dict[str, Any]:
    month = month or _current_month()
    data = _load_raw()
    bucket = data.get(month) or {"spent": 0.0, "generations": 0, "log": []}
    return {
        "month": month,
        "spent": float(bucket.get("spent", 0.0)),
        "generations": int(bucket.get("generations", 0)),
        "log": list(bucket.get("log", [])),
    }


def record_generation(
    model: str,
    mode: str,
    duration: int,
    cost: float,
    youtube_url: Optional[str] = None,
    drive_url: Optional[str] = None,
    shots: int = 1,
    total_duration: Optional[int] = None,
) -> Dict[str, Any]:
    month = _current_month()
    data = _load_raw()
    bucket = _ensure_month(data, month)
    entry: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "mode": mode,
        "shots": shots,
        "duration": total_duration if total_duration is not None else duration,
        "cost": round(float(cost), 4),
        "youtube_url": youtube_url,
        "drive_url": drive_url,
    }
    bucket["log"].append(entry)
    bucket["spent"] = round(float(bucket.get("spent", 0.0)) + float(cost), 4)
    bucket["generations"] = int(bucket.get("generations", 0)) + 1
    _atomic_write_json(COST_TRACKER_PATH, data)
    return entry


def update_last_entry_youtube(youtube_url: str) -> None:
    month = _current_month()
    data = _load_raw()
    bucket = data.get(month)
    if not bucket or not bucket.get("log"):
        return
    bucket["log"][-1]["youtube_url"] = youtube_url
    _atomic_write_json(COST_TRACKER_PATH, data)


def recent_entries(limit: int = 5) -> List[Dict[str, Any]]:
    data = _load_raw()
    entries: List[Dict[str, Any]] = []
    for month in sorted(data.keys(), reverse=True):
        for entry in reversed(data[month].get("log", [])):
            entries.append(entry)
            if len(entries) >= limit:
                return entries
    return entries
