"""Google Drive resumable upload."""
from __future__ import annotations

import asyncio
import io
from typing import Dict, Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from config import GOOGLE_DRIVE_FOLDER_NAME, logger
from services.google_auth import get_credentials


def _service():
    return build("drive", "v3", credentials=get_credentials(), cache_discovery=False)


def _ensure_folder_sync(name: str) -> str:
    service = _service()
    query = (
        "mimeType='application/vnd.google-apps.folder' "
        f"and name='{name}' and trashed=false"
    )
    res = service.files().list(q=query, fields="files(id,name)", pageSize=1).execute()
    files = res.get("files") or []
    if files:
        return files[0]["id"]
    folder = (
        service.files()
        .create(
            body={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
            },
            fields="id",
        )
        .execute()
    )
    return folder["id"]


def _upload_sync(
    data: bytes, name: str, folder_id: str, mime: str = "video/mp4"
) -> Tuple[str, str]:
    service = _service()
    media = MediaIoBaseUpload(
        io.BytesIO(data), mimetype=mime, chunksize=5 * 1024 * 1024, resumable=True
    )
    request = service.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=media,
        fields="id,webViewLink",
    )
    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
        except HttpError as exc:
            raise RuntimeError(f"Drive upload failed: {exc}") from exc
    file_id = response["id"]
    try:
        service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
            fields="id",
        ).execute()
    except HttpError as exc:
        logger.warning("Failed to set public permission on %s: %s", file_id, exc)
    link = f"https://drive.google.com/file/d/{file_id}/view"
    return file_id, link


def _download_sync(file_id: str) -> bytes:
    service = _service()
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    from googleapiclient.http import MediaIoBaseDownload

    downloader = MediaIoBaseDownload(buffer, request, chunksize=5 * 1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


async def upload_video(
    data: bytes,
    filename: str,
    folder_name: Optional[str] = None,
) -> Dict[str, str]:
    folder_name = folder_name or GOOGLE_DRIVE_FOLDER_NAME
    loop = asyncio.get_running_loop()
    folder_id = await loop.run_in_executor(None, _ensure_folder_sync, folder_name)
    file_id, link = await loop.run_in_executor(
        None, _upload_sync, data, filename, folder_id
    )
    return {"file_id": file_id, "link": link, "folder_id": folder_id}


async def download_video(file_id: str) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _download_sync, file_id)
