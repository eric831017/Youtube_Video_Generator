"""FFmpeg-based video segment merging for multi-shot generation."""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time

from telegram.ext import ContextTypes

from config import logger
from handlers.common import get_session
from models.session import State, UserSession


def get_video_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    for stream in streams:
        if "duration" in stream:
            return float(stream["duration"])
    raise ValueError(f"Cannot determine duration for {path}")


def merge_segments(
    segment_paths: list,
    output_path: str,
    fade_duration: float = 0.5,
) -> None:
    if len(segment_paths) == 1:
        import shutil
        shutil.copy(segment_paths[0], output_path)
        return

    durations = [get_video_duration(p) for p in segment_paths]

    inputs: list = []
    for p in segment_paths:
        inputs += ["-i", p]

    # Build xfade filter chain
    filter_parts = []
    prev_label = "[0:v]"
    offset = durations[0] - fade_duration

    for i in range(1, len(segment_paths)):
        is_last = i == len(segment_paths) - 1
        out_label = "[vout]" if is_last else f"[v{i}]"
        filter_parts.append(
            f"{prev_label}[{i}:v]xfade=transition=fade:"
            f"duration={fade_duration}:offset={offset:.3f}{out_label}"
        )
        prev_label = f"[v{i}]"
        if not is_last:
            offset += durations[i] - fade_duration

    filter_complex = ";".join(filter_parts)
    cmd = (
        ["ffmpeg"]
        + inputs
        + [
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "fast",
            "-an",
            output_path,
            "-y",
        ]
    )
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace")[:500])


def cleanup_temp_files(session: UserSession) -> None:
    for path in session.segment_paths:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
    session.segment_paths.clear()


async def merge_and_continue(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
) -> None:
    session: UserSession = get_session(context, user_id)
    session.state = State.MERGING
    await context.bot.send_message(chat_id, "🔧 合併影片段落中...")

    output_path = f"/tmp/vbot_merged_{user_id}_{int(time.time())}.mp4"
    try:
        merge_segments(session.segment_paths, output_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("FFmpeg merge failed")
        await context.bot.send_message(
            chat_id, f"❌ 影片合併失敗：{str(exc)[:200]}"
        )
        cleanup_temp_files(session)
        session.reset()
        return

    cleanup_temp_files(session)
    session.merged_video_path = output_path

    from handlers.upload import start_drive_upload_merged  # avoid circular at module level
    settings_module = __import__("config").load_settings()
    await start_drive_upload_merged(
        context,
        chat_id,
        user_id,
        settings_module["default_model"],
        session.total_duration,
        len(session.storyboard),
    )
