"""Shared helpers for handlers: auth check, MarkdownV2 escaping, formatting."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from config import MODELS, TELEGRAM_ALLOWED_USER_IDS
from models.session import SessionStore, UserSession

_MDV2_SPECIAL = r"_*[]()~`>#+-=|{}.!\\"


def md2(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    return "".join("\\" + ch if ch in _MDV2_SPECIAL else ch for ch in str(text))


def md2_url(text: str, url: str) -> str:
    escaped_text = md2(text)
    escaped_url = re.sub(r"([\\\)])", r"\\\1", url)
    return f"[{escaped_text}]({escaped_url})"


def is_authorized(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    return user.id in TELEGRAM_ALLOWED_USER_IDS


def get_store(context: ContextTypes.DEFAULT_TYPE) -> SessionStore:
    store: Optional[SessionStore] = context.application.bot_data.get("session_store")
    if store is None:
        store = SessionStore()
        context.application.bot_data["session_store"] = store
    return store


def get_session(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> UserSession:
    return get_store(context).get(user_id)


def mode_emoji_label(image_mode: Optional[str]) -> str:
    if image_mode == "i2v":
        return "🎬 Image-to-Video"
    if image_mode == "ref":
        return "🎨 Text+Image 參考"
    return "📝 文字生成"


def mode_code(image_mode: Optional[str]) -> str:
    if image_mode == "i2v":
        return "i2v"
    if image_mode == "ref":
        return "ref"
    return "t2v"


def model_label(model_key: str) -> str:
    return MODELS.get(model_key, {}).get("label", model_key)


def remaining_minutes(expires_at: Optional[datetime]) -> int:
    if expires_at is None:
        return 0
    delta = expires_at - datetime.now()
    secs = int(delta.total_seconds())
    if secs <= 0:
        return 0
    return max(1, (secs + 59) // 60)


async def reject_unauthorized(update: Update) -> bool:
    if is_authorized(update):
        return False
    if update.effective_message:
        await update.effective_message.reply_text(
            "🚫 Sorry, this bot is restricted to a single authorized user."
        )
    return True
