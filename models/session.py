"""UserSession dataclass, state constants, and a thread-safe session store."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


class State:
    IDLE = "IDLE"
    IMAGE_RECEIVED = "IMAGE_RECEIVED"
    AWAITING_PROMPT = "AWAITING_PROMPT"
    OPTIMIZING = "OPTIMIZING"
    AWAITING_CONFIRM = "AWAITING_CONFIRM"
    GENERATING = "GENERATING"
    UPLOADING_DRIVE = "UPLOADING_DRIVE"
    AWAITING_YOUTUBE_CONFIRM = "AWAITING_YOUTUBE_CONFIRM"
    UPLOADING_YOUTUBE = "UPLOADING_YOUTUBE"


@dataclass
class UserSession:
    state: str = State.IDLE
    raw_prompt: str = ""
    optimized_prompt: str = ""
    image_data: Optional[str] = None
    image_mime: Optional[str] = None
    image_mode: Optional[str] = None
    image_fal_url: Optional[str] = None
    image_expires_at: Optional[datetime] = None
    fal_request_id: Optional[str] = None
    drive_file_id: Optional[str] = None
    drive_link: Optional[str] = None
    video_url: Optional[str] = None
    estimated_cost: float = 0.0
    actual_cost: float = 0.0
    generation_start_time: Optional[datetime] = None
    polling_task: Optional[asyncio.Task] = field(default=None, repr=False)

    def clear_image(self) -> None:
        self.image_data = None
        self.image_mime = None
        self.image_mode = None
        self.image_fal_url = None
        self.image_expires_at = None

    def reset(self) -> None:
        task = self.polling_task
        self.state = State.IDLE
        self.raw_prompt = ""
        self.optimized_prompt = ""
        self.clear_image()
        self.fal_request_id = None
        self.drive_file_id = None
        self.drive_link = None
        self.video_url = None
        self.estimated_cost = 0.0
        self.actual_cost = 0.0
        self.generation_start_time = None
        self.polling_task = None
        if task is not None and not task.done():
            task.cancel()


class SessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[int, UserSession] = {}
        self._lock = asyncio.Lock()

    def get(self, user_id: int) -> UserSession:
        session = self._sessions.get(user_id)
        if session is None:
            session = UserSession()
            self._sessions[user_id] = session
        return session

    def all(self) -> Dict[int, UserSession]:
        return dict(self._sessions)

    async def locked(self) -> "SessionStore":
        await self._lock.acquire()
        return self

    def release(self) -> None:
        if self._lock.locked():
            self._lock.release()
