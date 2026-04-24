"""Telegram bot entry point: wiring handlers, TTL task, polling loop."""
from __future__ import annotations

import asyncio
from datetime import datetime

from telegram import Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import TELEGRAM_BOT_TOKEN, load_settings, logger, validate_env
from handlers.common import get_store, reject_unauthorized
from handlers.generate import handle_confirm
from handlers.prompt import (
    handle_cancel_callback,
    handle_image,
    handle_mode_choice,
    handle_rewrite,
    handle_text,
)
from handlers.settings import (
    auth_command,
    budget_command,
    history_command,
    settings_callback,
    settings_command,
)
from handlers.upload import handle_youtube_choice
from models.session import State
from services.fal_client import warn_unverified_endpoints

WELCOME_MESSAGE = (
    "👋 *歡迎使用 AI 影片生成機器人！*\n\n"
    "*使用流程：*\n"
    "1\\. 傳送文字（文字生成）或先傳圖片再文字（影像生成）\n"
    "2\\. Bot 自動用 GPT\\-4o 優化 Prompt 並顯示設定\n"
    "3\\. 確認後 fal\\.ai 生成影片（3–8 分鐘）\n"
    "4\\. 影片自動上傳 Google Drive，可選擇發布 YouTube\n\n"
    "*指令：*\n"
    "/settings \\- 管理預設參數\n"
    "/budget \\- 本月費用報告\n"
    "/history \\- 最近 5 筆生成記錄\n"
    "/cancel \\- 取消目前流程\n"
    "/auth \\- Google OAuth 授權（首次使用需執行）"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    from telegram.constants import ParseMode
    await update.effective_message.reply_text(
        WELCOME_MESSAGE, parse_mode=ParseMode.MARKDOWN_V2
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    user = update.effective_user
    if user is None:
        return
    session = get_store(context).get(user.id)
    session.reset()
    await update.effective_message.reply_text(
        "✅ 已取消目前流程，所有暫存資料（含圖片）已清除。"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_command(update, context)


async def image_ttl_loop(application: Application) -> None:
    store = get_store_from_app(application)
    while True:
        try:
            await asyncio.sleep(60)
            now = datetime.now()
            for user_id, session in store.all().items():
                if session.image_expires_at and session.image_expires_at < now:
                    was_in_image_flow = session.state in (
                        State.IMAGE_RECEIVED,
                        State.AWAITING_PROMPT,
                    )
                    session.clear_image()
                    if was_in_image_flow:
                        session.state = State.IDLE
                        try:
                            await application.bot.send_message(
                                user_id,
                                "⏰ 暫存圖片已自動清除（逾時 10 分鐘），請重新傳送圖片。",
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "Failed to notify %s about TTL expiry: %s",
                                user_id,
                                exc,
                            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("TTL loop error, continuing")


def get_store_from_app(application: Application):
    from models.session import SessionStore

    store = application.bot_data.get("session_store")
    if store is None:
        store = SessionStore()
        application.bot_data["session_store"] = store
    return store


async def _on_startup(application: Application) -> None:
    load_settings()
    warn_unverified_endpoints()
    get_store_from_app(application)
    application.bot_data["ttl_task"] = asyncio.create_task(
        image_ttl_loop(application)
    )
    logger.info("Bot started and TTL loop running")


async def _on_shutdown(application: Application) -> None:
    task = application.bot_data.get("ttl_task")
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    logger.info("Bot shutdown complete")


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"❌ 發生未預期錯誤：{context.error}"
            )
        except Exception:  # noqa: BLE001
            pass


def build_application() -> Application:
    validate_env()
    builder = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).rate_limiter(
        AIORateLimiter()
    )
    application = builder.post_init(_on_startup).post_shutdown(
        _on_shutdown
    ).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("budget", budget_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("auth", auth_command))

    application.add_handler(CallbackQueryHandler(handle_mode_choice, pattern=r"^imgmode:"))
    application.add_handler(CallbackQueryHandler(handle_confirm, pattern=r"^confirm$"))
    application.add_handler(CallbackQueryHandler(handle_rewrite, pattern=r"^rewrite$"))
    application.add_handler(CallbackQueryHandler(handle_cancel_callback, pattern=r"^cancel$"))
    application.add_handler(CallbackQueryHandler(handle_youtube_choice, pattern=r"^yt:"))
    application.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^set(v)?:"))

    application.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    application.add_error_handler(_error_handler)
    return application


def main() -> None:
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main()
