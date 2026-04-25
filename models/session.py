"""UserSession dataclass, state constants, and a thread-safe session store."""
from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


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
    # multi-shot states
    CLASSIFYING = "CLASSIFYING"
    STORYBOARDING = "STORYBOARDING"
    AWAITING_STORYBOARD_CONFIRM = "AWAITING_STORYBOARD_CONFIRM"
    AWAITING_STORYBOARD_REVISION = "AWAITING_STORYBOARD_REVISION"
    BUDGET_CHECK_MULTI = "BUDGET_CHECK_MULTI"
    GENERATING_MULTI = "GENERATING_MULTI"
    MERGING = "MERGING"


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
    # multi-shot fields
    shot_type: str = "single"
    style_anchor: Optional[str] = None
    scene_anchor: Optional[str] = None
    storyboard: List[dict] = field(default_factory=list)
    total_duration: int = 0
    current_shot_index: int = 0
    segment_paths: List[str] = field(default_factory=list)
    last_frame_url: Optional[str] = None
    merged_video_path: Optional[str] = None

    def clear_image(self) -> None:
        self.image_data = None
        self.image_mime = None
        self.image_mode = None
        self.image_fal_url = None
        self.image_expires_at = None

    def reset(self) -> None:
        task = self.polling_task
        for path in self.segment_paths:
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
        if self.merged_video_path:
            with contextlib.suppress(FileNotFoundError):
                os.remove(self.merged_video_path)
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
        self.shot_type = "single"
        self.style_anchor = None
        self.scene_anchor = None
        self.storyboard = []
        self.total_duration = 0
        self.current_shot_index = 0
        self.segment_paths = []
        self.last_frame_url = None
        self.merged_video_path = None
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
