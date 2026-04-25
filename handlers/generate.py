"""Video generation: fal.ai submission, polling, handoff to upload pipeline."""
from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import time
from datetime import datetime
from typing import Any, Dict

from telegram import Update
from telegram.ext import ContextTypes

from config import MODELS, load_settings, logger
from handlers.common import get_session, reject_unauthorized
from handlers.upload import start_drive_upload
from models.session import State, UserSession
from services import fal_client

POLL_INTERVAL_SECS = 30
POLL_MAX_ATTEMPTS = 20


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    user = update.effective_user
    if user is None or query.message is None:
        return

    session = get_session(context, user.id)
    if session.state != State.AWAITING_CONFIRM or not session.optimized_prompt:
        await query.edit_message_text("⚠️ 此確認按鈕已過期，請重新開始。")
        session.reset()
        return

    settings = load_settings()
    model_key = settings["default_model"]
    model_cfg = MODELS.get(model_key)
    if model_cfg is None:
        await query.edit_message_text(f"❌ 模型 {model_key} 無效。")
        session.reset()
        return

    session.state = State.GENERATING
    session.generation_start_time = datetime.now()

    try:
        await query.edit_message_text("🎬 開始生成影片，預計需要 3–8 分鐘...")
    except Exception:  # noqa: BLE001
        pass

    chat_id = query.message.chat_id

    try:
        if session.image_mode == "i2v" and session.image_data and session.image_mime:
            fal_url = await fal_client.upload_image(
                session.image_data, session.image_mime
            )
            session.image_fal_url = fal_url
    except fal_client.FalError as exc:
        logger.exception("fal image upload failed")
        await context.bot.send_message(
            chat_id, f"❌ 圖片上傳 fal 失敗：{exc}"
        )
        session.reset()
        return

    duration = min(int(settings["default_duration"]), int(model_cfg["max_duration"]))
    payload: Dict[str, Any] = {
        "prompt": session.optimized_prompt,
        "duration": duration,
        "aspect_ratio": settings["default_ratio"],
        "resolution": settings["default_resolution"],
    }
    if session.image_mode == "i2v" and session.image_fal_url:
        payload["image_url"] = session.image_fal_url
        fal_id = model_cfg["i2v_fal_id"]
    else:
        fal_id = model_cfg["t2v_fal_id"]

    try:
        fal_req = await fal_client.submit_generation(fal_id, payload)
    except fal_client.FalError as exc:
        logger.exception("fal submission failed")
        await context.bot.send_message(chat_id, f"❌ 提交 fal 失敗：{exc}")
        session.reset()
        return

    session.fal_request_id = fal_req.request_id
    logger.info(
        "fal request_id=%s model=%s status_url=%s",
        fal_req.request_id, model_key, fal_req.status_url,
    )

    task = asyncio.create_task(
        _poll_generation(
            context, chat_id, user.id, fal_req, model_key, duration
        )
    )
    session.polling_task = task


async def _poll_generation(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    fal_req: fal_client.FalRequest,
    model_key: str,
    duration: int,
) -> None:
    session: UserSession = get_session(context, user_id)
    start = session.generation_start_time or datetime.now()
    request_id = fal_req.request_id
    try:
        for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
            await asyncio.sleep(POLL_INTERVAL_SECS)
            try:
                status = await fal_client.check_status(fal_req.status_url)
            except fal_client.FalError as exc:
                logger.warning("status poll error: %s", exc)
                continue

            status_code = (status.get("status") or "").upper()
            logger.info(
                "poll %d status=%s request_id=%s", attempt, status_code, request_id
            )

            if status_code in ("COMPLETED", "SUCCESS", "OK"):
                try:
                    result = await fal_client.get_result(fal_req.response_url)
                except fal_client.FalError as exc:
                    await context.bot.send_message(
                        chat_id, f"❌ 取得生成結果失敗：{exc}"
                    )
                    session.reset()
                    return
                video_url = fal_client.extract_video_url(result)
                if not video_url:
                    await context.bot.send_message(
                        chat_id, "❌ 生成完成但無法解析影片 URL。"
                    )
                    session.reset()
                    return
                session.video_url = video_url
                await start_drive_upload(
                    context, chat_id, user_id, model_key, duration
                )
                return

            if status_code in ("FAILED", "ERROR"):
                error_message = (
                    status.get("error")
                    or status.get("logs")
                    or status.get("detail")
                    or "未知錯誤"
                )
                await context.bot.send_message(
                    chat_id, f"❌ 生成失敗：{error_message}"
                )
                session.reset()
                return

            if attempt % 2 == 0:
                elapsed = (datetime.now() - start).total_seconds() / 60
                try:
                    await context.bot.send_message(
                        chat_id, f"⏳ 生成中... (已等待 {elapsed:.1f} 分鐘)"
                    )
                except Exception:  # noqa: BLE001
                    pass

        await context.bot.send_message(chat_id, "⏰ 生成逾時，請稍後再試")
        session.reset()
    except asyncio.CancelledError:
        logger.info("Polling cancelled for request %s", request_id)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected polling error")
        try:
            await context.bot.send_message(
                chat_id, f"❌ 生成過程發生錯誤：{exc}"
            )
        except Exception:  # noqa: BLE001
            pass
        session.reset()


def _cleanup_segments(session) -> None:
    for path in session.segment_paths:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
    session.segment_paths.clear()


async def generate_multi_shot_task(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
) -> None:
    """Sequential multi-shot generation with last-frame chaining."""
    from handlers.merge import merge_and_continue  # avoid circular at module level

    session = get_session(context, user_id)
    settings = load_settings()
    model_key = settings["default_model"]
    model_cfg = MODELS.get(model_key)
    if model_cfg is None:
        await context.bot.send_message(chat_id, f"❌ 模型 {model_key} 無效。")
        session.reset()
        return

    session.last_frame_url = None

    try:
        for i, shot in enumerate(session.storyboard):
            session.current_shot_index = i
            shot_num = i + 1
            total = len(session.storyboard)

            await context.bot.send_message(
                chat_id,
                f"🎬 生成第 {shot_num}/{total} 段...（預計 3–8 分鐘）",
            )

            duration = min(int(shot["duration"]), int(model_cfg["max_duration"]))
            full_prompt = (
                f"{session.style_anchor}, {session.scene_anchor}, "
                f"{shot['prompt']}, {shot['transition_note']}"
            )

            if i == 0 and session.image_mode == "i2v" and session.image_fal_url:
                fal_id = model_cfg["i2v_fal_id"]
                payload: Dict[str, Any] = {
                    "prompt": full_prompt,
                    "image_url": session.image_fal_url,
                    "duration": str(duration),
                    "aspect_ratio": settings["default_ratio"],
                    "resolution": settings["default_resolution"],
                }
            elif i == 0:
                fal_id = model_cfg["t2v_fal_id"]
                payload = {
                    "prompt": full_prompt,
                    "duration": str(duration),
                    "aspect_ratio": settings["default_ratio"],
                    "resolution": settings["default_resolution"],
                }
            else:
                fal_id = model_cfg["i2v_fal_id"]
                payload = {
                    "prompt": full_prompt,
                    "image_url": session.last_frame_url,
                    "duration": str(duration),
                    "aspect_ratio": settings["default_ratio"],
                    "resolution": settings["default_resolution"],
                }

            def _make_cb(n: int, t: int):
                async def _cb(msg: str) -> None:
                    await context.bot.send_message(chat_id, f"[{n}/{t}] {msg}")
                return _cb

            try:
                video_url = await fal_client.generate_and_poll(
                    fal_id, payload, progress_callback=_make_cb(shot_num, total)
                )
            except fal_client.FalError as exc:
                logger.exception("fal multi-shot failed at shot %d", shot_num)
                await context.bot.send_message(
                    chat_id, f"❌ 第 {shot_num} 段生成失敗：{exc}"
                )
                _cleanup_segments(session)
                session.reset()
                return

            segment_path = f"/tmp/vbot_seg_{user_id}_{i}_{int(time.time())}.mp4"
            try:
                await fal_client.download_video_to_file(video_url, segment_path)
            except fal_client.FalError as exc:
                await context.bot.send_message(
                    chat_id, f"❌ 第 {shot_num} 段影片下載失敗：{exc}"
                )
                _cleanup_segments(session)
                session.reset()
                return

            session.segment_paths.append(segment_path)

            # Extract last frame for chaining (skip on final shot)
            if i < total - 1:
                frame_path = f"/tmp/vbot_frame_{user_id}_{i}_{int(time.time())}.jpg"
                result = subprocess.run(
                    [
                        "ffmpeg", "-sseof", "-0.1", "-i", segment_path,
                        "-frames:v", "1", "-q:v", "2", frame_path, "-y",
                    ],
                    capture_output=True,
                )
                if result.returncode != 0:
                    err = result.stderr.decode(errors="replace")[:200]
                    await context.bot.send_message(
                        chat_id,
                        f"❌ 末幀截取失敗（第 {shot_num} 段）：{err}",
                    )
                    _cleanup_segments(session)
                    session.reset()
                    return

                try:
                    session.last_frame_url = await fal_client.upload_image_file(frame_path)
                except fal_client.FalError as exc:
                    await context.bot.send_message(
                        chat_id, f"❌ 末幀上傳失敗（第 {shot_num} 段）：{exc}"
                    )
                    _cleanup_segments(session)
                    session.reset()
                    return
                finally:
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(frame_path)

        await merge_and_continue(context, chat_id, user_id)

    except asyncio.CancelledError:
        logger.info("Multi-shot task cancelled for user %s", user_id)
        _cleanup_segments(session)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected multi-shot error")
        try:
            await context.bot.send_message(chat_id, f"❌ 多段生成發生錯誤：{exc}")
        except Exception:  # noqa: BLE001
            pass
        _cleanup_segments(session)
        session.reset()
