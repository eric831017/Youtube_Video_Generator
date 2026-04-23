"""Video generation: fal.ai submission, polling, handoff to upload pipeline."""
from __future__ import annotations

import asyncio
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
