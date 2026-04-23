"""Async fal.ai queue API wrapper."""
from __future__ import annotations

import asyncio
import base64
from typing import Any, Dict, Optional

import httpx

from config import FAL_API_KEY, logger

FAL_STORAGE_INITIATE = "https://rest.alpha.fal.ai/storage/upload/initiate"
FAL_STORAGE_LEGACY = "https://fal.run/storage/upload"
FAL_QUEUE_BASE = "https://queue.fal.run"

API_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
DOWNLOAD_TIMEOUT = httpx.Timeout(120.0, connect=15.0)


class FalError(RuntimeError):
    pass


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Key {FAL_API_KEY}"}


async def upload_image(image_b64: str, mime: str) -> str:
    """Upload a base64 image to fal storage, return public URL."""
    raw = base64.b64decode(image_b64)
    filename = "image.png" if mime == "image/png" else "image.jpg"

    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        try:
            resp = await client.post(
                FAL_STORAGE_INITIATE,
                headers={**_headers(), "Content-Type": "application/json"},
                json={"file_name": filename, "content_type": mime},
            )
            if resp.status_code < 400:
                data = resp.json()
                upload_url = data.get("upload_url")
                file_url = data.get("file_url") or data.get("url")
                if upload_url and file_url:
                    put = await client.put(
                        upload_url,
                        content=raw,
                        headers={"Content-Type": mime},
                    )
                    if put.status_code >= 400:
                        raise FalError(
                            f"fal storage PUT failed: {put.status_code} {put.text}"
                        )
                    return file_url
        except httpx.HTTPError as exc:
            logger.info("fal storage initiate unavailable (%s), falling back", exc)

        resp = await client.post(
            FAL_STORAGE_LEGACY,
            headers=_headers(),
            files={"file": (filename, raw, mime)},
        )
    if resp.status_code >= 400:
        raise FalError(f"fal storage upload failed: {resp.status_code} {resp.text}")
    body = resp.json()
    url = body.get("url") or body.get("file_url")
    if not url:
        raise FalError(f"fal storage upload returned no url: {body}")
    return url


class FalRequest:
    __slots__ = ("request_id", "status_url", "response_url")

    def __init__(self, request_id: str, status_url: str, response_url: str) -> None:
        self.request_id = request_id
        self.status_url = status_url
        self.response_url = response_url


async def submit_generation(fal_id: str, payload: Dict[str, Any]) -> FalRequest:
    """Submit to fal queue, return FalRequest with URLs from the response."""
    url = f"{FAL_QUEUE_BASE}/{fal_id}"
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        for attempt in range(2):
            resp = await client.post(url, headers=_headers(), json=payload)
            if resp.status_code == 429 and attempt == 0:
                logger.warning("fal 429 rate limit, waiting 60s before retry")
                await asyncio.sleep(60)
                continue
            if resp.status_code >= 400:
                raise FalError(
                    f"fal submit failed: {resp.status_code} {resp.text}"
                )
            break
    data = resp.json()
    request_id = data.get("request_id")
    if not request_id:
        raise FalError(f"fal submit returned no request_id: {data}")
    # Use URLs returned by the API; fall back to constructed URLs if absent
    status_url = (
        data.get("status_url")
        or f"{FAL_QUEUE_BASE}/{fal_id}/requests/{request_id}/status"
    )
    response_url = (
        data.get("response_url")
        or f"{FAL_QUEUE_BASE}/{fal_id}/requests/{request_id}"
    )
    return FalRequest(request_id, status_url, response_url)


async def check_status(status_url: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        resp = await client.get(status_url, headers=_headers(), params={"logs": 1})
    if resp.status_code >= 400:
        raise FalError(f"fal status failed: {resp.status_code} {resp.text}")
    return resp.json()


async def get_result(response_url: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        resp = await client.get(response_url, headers=_headers())
    if resp.status_code >= 400:
        raise FalError(f"fal result failed: {resp.status_code} {resp.text}")
    return resp.json()


def extract_video_url(result: Dict[str, Any]) -> Optional[str]:
    """Extract video URL from fal response (different models have different shapes)."""
    video = result.get("video")
    if isinstance(video, dict) and video.get("url"):
        return video["url"]
    if isinstance(video, str):
        return video
    for key in ("video_url", "url", "output"):
        value = result.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
        if isinstance(value, dict) and value.get("url"):
            return value["url"]
    videos = result.get("videos")
    if isinstance(videos, list) and videos:
        first = videos[0]
        if isinstance(first, dict) and first.get("url"):
            return first["url"]
        if isinstance(first, str):
            return first
    return None


async def download_video(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise FalError(f"video download failed: {resp.status_code}")
    return resp.content
