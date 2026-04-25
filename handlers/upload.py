"""Google Drive + YouTube upload handlers."""
from __future__ import annotations

import contextlib
import os
import re
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import load_settings, logger
from handlers.common import (
    get_session,
    md2,
    md2_url,
    mode_code,
    reject_unauthorized,
)
from models import cost_tracker
from models.session import State
from services import drive_client, fal_client, youtube_client


def _sanitize_title(text: str, limit: int = 80) -> str:
    cleaned = re.sub(r"[\r\n]+", " ", text).strip()
    cleaned = re.sub(r"[^\w\s\-.,!?()'\"一-鿿]", "", cleaned, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip()
    return cleaned or "AI Generated Video"


async def start_drive_upload(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    model_key: str,
    duration: int,
) -> None:
    session = get_session(context, user_id)
    session.state = State.UPLOADING_DRIVE
    await context.bot.send_message(chat_id, "📤 上傳到 Google Drive 中...")

    try:
        video_bytes = await fal_client.download_video(session.video_url or "")
    except fal_client.FalError as exc:
        await context.bot.send_message(chat_id, f"❌ 影片下載失敗：{exc}")
        session.reset()
        return

    filename = (
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{model_key}.mp4"
    )

    for attempt in range(2):
        try:
            result = await drive_client.upload_video(video_bytes, filename)
            break
        except Exception as exc:  # noqa: BLE001
            logger.exception("Drive upload attempt %d failed", attempt + 1)
            if attempt == 1:
                await context.bot.send_message(
                    chat_id, f"❌ Google Drive 上傳失敗：{exc}"
                )
                session.reset()
                return

    session.drive_file_id = result["file_id"]
    session.drive_link = result["link"]

    mode_str = mode_code(session.image_mode)
    cost = float(session.estimated_cost)
    cost_tracker.record_generation(
        model=model_key,
        mode=mode_str,
        duration=duration,
        cost=cost,
        youtube_url=None,
        drive_url=session.drive_link,
    )
    session.actual_cost = cost
    session.state = State.AWAITING_YOUTUBE_CONFIRM

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚀 上傳 YouTube", callback_data="yt:yes")],
            [InlineKeyboardButton("❌ 不了，謝謝", callback_data="yt:no")],
        ]
    )
    body = (
        f"✅ *影片已生成完成\\!*\n\n"
        f"🔗 {md2_url('Google Drive 預覽連結', session.drive_link)}\n\n"
        f"是否要上傳至 YouTube？"
    )
    await context.bot.send_message(
        chat_id,
        body,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
        disable_web_page_preview=False,
    )


async def start_drive_upload_merged(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    model_key: str,
    total_duration: int,
    shots: int,
) -> None:
    """Drive upload for a locally-merged multi-shot video."""
    session = get_session(context, user_id)
    session.state = State.UPLOADING_DRIVE
    await context.bot.send_message(chat_id, "📤 上傳合併影片到 Google Drive 中...")

    merged_path = session.merged_video_path
    if not merged_path or not os.path.exists(merged_path):
        await context.bot.send_message(chat_id, "❌ 找不到合併後的影片檔案。")
        session.reset()
        return

    with open(merged_path, "rb") as fh:
        video_bytes = fh.read()
    with contextlib.suppress(FileNotFoundError):
        os.remove(merged_path)
    session.merged_video_path = None

    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{model_key}_multi.mp4"
    for attempt in range(2):
        try:
            result = await drive_client.upload_video(video_bytes, filename)
            break
        except Exception as exc:  # noqa: BLE001
            logger.exception("Drive upload attempt %d failed", attempt + 1)
            if attempt == 1:
                await context.bot.send_message(
                    chat_id, f"❌ Google Drive 上傳失敗：{exc}"
                )
                session.reset()
                return

    session.drive_file_id = result["file_id"]
    session.drive_link = result["link"]

    mode_str = "multi_" + mode_code(session.image_mode)
    cost = float(session.estimated_cost)
    cost_tracker.record_generation(
        model=model_key,
        mode=mode_str,
        duration=total_duration,
        cost=cost,
        youtube_url=None,
        drive_url=session.drive_link,
        shots=shots,
        total_duration=total_duration,
    )
    session.actual_cost = cost
    session.state = State.AWAITING_YOUTUBE_CONFIRM

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 上傳 YouTube", callback_data="yt:yes")],
        [InlineKeyboardButton("❌ 不了，謝謝", callback_data="yt:no")],
    ])
    body = (
        f"✅ *{shots} 段影片已合併完成\\!*\n\n"
        f"🔗 {md2_url('Google Drive 預覽連結', session.drive_link)}\n\n"
        f"是否要上傳至 YouTube？"
    )
    await context.bot.send_message(
        chat_id,
        body,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
        disable_web_page_preview=False,
    )


async def handle_youtube_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    user = update.effective_user
    if user is None or query.message is None:
        return
    session = get_session(context, user.id)

    choice = query.data.split(":", 1)[1]
    if session.state != State.AWAITING_YOUTUBE_CONFIRM:
        await query.edit_message_text("⚠️ 此選項已過期。")
        return

    if choice == "no":
        try:
            await query.edit_message_text(
                "👌 好的，本次不上傳 YouTube。Drive 連結仍有效。"
            )
        except Exception:  # noqa: BLE001
            pass
        session.reset()
        return

    session.state = State.UPLOADING_YOUTUBE
    chat_id = query.message.chat_id
    await query.edit_message_text("🚀 上傳 YouTube 中，請稍候...")

    settings = load_settings()
    title = _sanitize_title(session.optimized_prompt)
    description = (
        f"{session.optimized_prompt}\n\nGenerated by AI via fal.ai"
    )

    try:
        if session.drive_file_id:
            video_bytes = await drive_client.download_video(session.drive_file_id)
        else:
            video_bytes = await fal_client.download_video(session.video_url or "")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch video binary for YouTube")
        await context.bot.send_message(
            chat_id,
            f"❌ 影片下載失敗，無法上傳 YouTube：{exc}\n"
            f"Drive 連結仍可使用：{session.drive_link}",
        )
        session.reset()
        return

    try:
        result = await youtube_client.upload_video(
            video_bytes,
            title=title,
            description=description,
            category_id=str(settings["youtube_default_category"]),
            privacy=str(settings["youtube_default_privacy"]),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("YouTube upload failed")
        await context.bot.send_message(
            chat_id,
            f"❌ YouTube 上傳失敗：{exc}\n"
            f"Drive 連結仍可使用：{session.drive_link}",
        )
        session.reset()
        return

    cost_tracker.update_last_entry_youtube(result["url"])
    await context.bot.send_message(
        chat_id,
        f"✅ 已發布！\n{result['url']}",
    )
    session.reset()
