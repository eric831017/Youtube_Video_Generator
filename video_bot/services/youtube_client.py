"""YouTube Data API v3 resumable upload."""
from __future__ import annotations

import asyncio
import io
from typing import Dict

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from config import logger
from services.google_auth import get_credentials


def _service():
    return build("youtube", "v3", credentials=get_credentials(), cache_discovery=False)


def _upload_sync(
    data: bytes,
    title: str,
    description: str,
    category_id: str,
    privacy: str,
) -> Dict[str, str]:
    service = _service()
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaIoBaseUpload(
        io.BytesIO(data),
        mimetype="video/mp4",
        chunksize=5 * 1024 * 1024,
        resumable=True,
    )
    request = service.videos().insert(
        part="snippet,status", body=body, media_body=media
    )
    response = None
    while response is None:
        try:
            _, response = request.next_chunk()
        except HttpError as exc:
            raise RuntimeError(f"YouTube upload failed: {exc}") from exc
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube returned no id: {response}")
    logger.info("Uploaded YouTube video %s", video_id)
    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }


async def upload_video(
    data: bytes,
    title: str,
    description: str,
    category_id: str,
    privacy: str,
) -> Dict[str, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _upload_sync, data, title, description, category_id, privacy
    )
