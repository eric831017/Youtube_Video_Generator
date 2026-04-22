"""Handlers for image input, text input (prompt optimization), confirmation."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import MAX_IMAGE_BYTES, MODELS, load_settings, logger
from handlers.common import (
    get_session,
    md2,
    mode_emoji_label,
    model_label,
    reject_unauthorized,
    remaining_minutes,
)
from models.cost_tracker import get_monthly_spent
from models.session import State
from services.optimizer import optimize_prompt


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    session = get_session(context, user.id)

    if session.state not in (State.IDLE, State.IMAGE_RECEIVED, State.AWAITING_PROMPT):
        await message.reply_text(
            "⚠️ 目前正在處理其他流程，請先 /cancel 後再傳送圖片。"
        )
        return

    file_ref = None
    mime = "image/jpeg"
    if message.photo:
        file_ref = await message.photo[-1].get_file()
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_ref = await message.document.get_file()
        mime = message.document.mime_type or "image/jpeg"
    else:
        return

    if file_ref.file_size and file_ref.file_size > MAX_IMAGE_BYTES:
        await message.reply_text(
            f"❌ 圖片過大（{file_ref.file_size / 1024 / 1024:.1f} MB），"
            f"上限為 {MAX_IMAGE_BYTES // (1024 * 1024)} MB。"
        )
        return

    try:
        raw = bytes(await file_ref.download_as_bytearray())
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to download Telegram image")
        await message.reply_text(f"❌ 圖片下載失敗：{exc}")
        return

    if len(raw) > MAX_IMAGE_BYTES:
        await message.reply_text("❌ 圖片過大，請壓縮後重試（上限 20MB）。")
        return

    session.clear_image()
    session.image_data = base64.b64encode(raw).decode("ascii")
    session.image_mime = mime
    session.image_expires_at = datetime.now() + timedelta(minutes=10)
    session.state = State.IMAGE_RECEIVED

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎬 作為影片起始幀（Image-to-Video）", callback_data="imgmode:i2v")],
            [InlineKeyboardButton("🎨 作為風格／場景參考（Text+Image）", callback_data="imgmode:ref")],
            [InlineKeyboardButton("❌ 取消", callback_data="cancel")],
        ]
    )
    await message.reply_text(
        "🖼️ 圖片已收到！請選擇用途：",
        reply_markup=keyboard,
    )


async def handle_mode_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    user = update.effective_user
    if user is None:
        return
    session = get_session(context, user.id)

    _, mode = query.data.split(":", 1)
    if session.state != State.IMAGE_RECEIVED or session.image_data is None:
        await query.edit_message_text("⚠️ 此選項已過期，請重新傳送圖片。")
        session.reset()
        return
    if mode not in ("i2v", "ref"):
        return

    session.image_mode = mode
    session.state = State.AWAITING_PROMPT
    mins = remaining_minutes(session.image_expires_at)
    label = mode_emoji_label(mode)
    await query.edit_message_text(
        f"✅ 已選擇：{label}\n請輸入文字 Prompt（圖片將在 {mins} 分鐘後自動清除）"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    message = update.effective_message
    user = update.effective_user
    if message is None or message.text is None or user is None:
        return
    text = message.text.strip()
    if not text or text.startswith("/"):
        return

    session = get_session(context, user.id)
    if session.state not in (State.IDLE, State.AWAITING_PROMPT):
        await message.reply_text(
            "⚠️ 目前流程進行中，請等待或 /cancel 後重新開始。"
        )
        return

    session.raw_prompt = text
    session.state = State.OPTIMIZING
    progress = await message.reply_text("⏳ 優化 Prompt 中...")

    try:
        optimized = await optimize_prompt(
            raw_prompt=text,
            image_mode=session.image_mode,
            image_data=session.image_data,
            image_mime=session.image_mime,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Prompt optimization failed")
        session.optimized_prompt = text
        optimized = text
        await progress.edit_text(
            f"⚠️ Prompt 優化失敗（{exc}）。將使用原始輸入繼續。"
        )
    else:
        session.optimized_prompt = optimized
        try:
            await progress.delete()
        except Exception:  # noqa: BLE001
            pass

    settings = load_settings()
    model_key = settings["default_model"]
    model_cfg = MODELS.get(model_key)
    if model_cfg is None:
        await message.reply_text(
            f"❌ 預設模型 {model_key} 不存在，請使用 /settings 更改。"
        )
        session.reset()
        return

    duration = int(settings["default_duration"])
    max_duration = int(model_cfg.get("max_duration", duration))
    duration = min(duration, max_duration)
    estimated_cost = float(model_cfg["price_per_sec"]) * duration
    session.estimated_cost = estimated_cost

    monthly_spent = get_monthly_spent()
    budget = float(settings["monthly_budget_usd"])
    if monthly_spent + estimated_cost > budget:
        await message.reply_text(
            f"⚠️ 本月預算已達上限（已用 ${monthly_spent:.2f} / 上限 ${budget:.2f}）\n"
            "請用 /settings 調整預算上限。"
        )
        session.reset()
        return

    session.state = State.AWAITING_CONFIRM

    mode_label = mode_emoji_label(session.image_mode)
    lines = [
        f"🎬 *優化後 Prompt：*",
        md2(optimized),
        "",
        "⚙️ *本次生成設定*",
        f"├ 模式：{md2(mode_label)}",
        f"├ 模型：{md2(model_label(model_key))}",
        f"├ 比例：{md2(settings['default_ratio'])}",
        f"├ 時長：{md2(str(duration))} 秒",
        f"├ 解析度：{md2(settings['default_resolution'])}",
        f"├ 💰 預估費用：${md2(f'{estimated_cost:.3f}')}",
        f"└ 📊 本月用量：${md2(f'{monthly_spent:.2f}')} / ${md2(f'{budget:.2f}')}",
        "",
        md2("如需修改參數，請先 /cancel 後使用 /settings 調整。"),
    ]
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 確認生成", callback_data="confirm"),
                InlineKeyboardButton("✏️ 重寫 Prompt", callback_data="rewrite"),
            ],
            [InlineKeyboardButton("❌ 取消", callback_data="cancel")],
        ]
    )
    await message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
    )


async def handle_rewrite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    user = update.effective_user
    if user is None:
        return
    session = get_session(context, user.id)
    session.reset()
    await query.edit_message_text(
        "✏️ 已清除圖片與 Prompt。請重新傳送圖片或直接輸入新的文字 Prompt。"
    )


async def handle_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    user = update.effective_user
    if user is None:
        return
    session = get_session(context, user.id)
    session.reset()
    try:
        await query.edit_message_text("❌ 已取消，所有暫存資料已清除。")
    except Exception:  # noqa: BLE001
        pass
