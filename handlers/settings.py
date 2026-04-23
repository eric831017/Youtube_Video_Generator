"""/settings, /budget, /history, /auth commands."""
from __future__ import annotations

from datetime import datetime
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import (
    ASPECT_RATIOS,
    DURATIONS,
    MODELS,
    RESOLUTIONS,
    YOUTUBE_CATEGORIES,
    YOUTUBE_PRIVACY_OPTIONS,
    load_settings,
    update_setting,
)
from handlers.common import md2, model_label, reject_unauthorized
from models import cost_tracker
from services import google_auth


def _overview_text(settings: dict) -> str:
    cat = YOUTUBE_CATEGORIES.get(
        str(settings["youtube_default_category"]),
        str(settings["youtube_default_category"]),
    )
    budget_str = f"{float(settings['monthly_budget_usd']):.2f}"
    lines = [
        "⚙️ *目前設定*",
        "",
        f"預設模型：{md2(model_label(settings['default_model']))}",
        f"預設比例：{md2(settings['default_ratio'])}",
        f"預設時長：{md2(str(settings['default_duration']))} 秒",
        f"預設解析度：{md2(settings['default_resolution'])}",
        f"月預算上限：${md2(budget_str)}",
        f"YouTube 隱私：{md2(settings['youtube_default_privacy'].title())}",
        f"YouTube 分類：{md2(cat)}",
    ]
    return "\n".join(lines)


def _overview_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("修改模型", callback_data="set:model")],
        [InlineKeyboardButton("修改比例", callback_data="set:ratio")],
        [InlineKeyboardButton("修改時長", callback_data="set:duration")],
        [InlineKeyboardButton("修改解析度", callback_data="set:resolution")],
        [InlineKeyboardButton("修改月預算", callback_data="set:budget")],
        [InlineKeyboardButton("修改 YouTube 隱私", callback_data="set:privacy")],
        [InlineKeyboardButton("修改 YouTube 分類", callback_data="set:category")],
        [InlineKeyboardButton("✅ 關閉", callback_data="set:close")],
    ]
    return InlineKeyboardMarkup(rows)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    settings = load_settings()
    await update.effective_message.reply_text(
        _overview_text(settings),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=_overview_keyboard(),
    )


def _field_options(field: str) -> List[InlineKeyboardButton]:
    if field == "model":
        return [
            InlineKeyboardButton(cfg["label"], callback_data=f"setv:model:{key}")
            for key, cfg in MODELS.items()
        ]
    if field == "ratio":
        return [
            InlineKeyboardButton(r, callback_data=f"setv:ratio:{r}")
            for r in ASPECT_RATIOS
        ]
    if field == "duration":
        return [
            InlineKeyboardButton(f"{d}s", callback_data=f"setv:duration:{d}")
            for d in DURATIONS
        ]
    if field == "resolution":
        return [
            InlineKeyboardButton(r, callback_data=f"setv:resolution:{r}")
            for r in RESOLUTIONS
        ]
    if field == "budget":
        return [
            InlineKeyboardButton(f"${v}", callback_data=f"setv:budget:{v}")
            for v in (5, 10, 20, 50, 100)
        ]
    if field == "privacy":
        return [
            InlineKeyboardButton(p.title(), callback_data=f"setv:privacy:{p}")
            for p in YOUTUBE_PRIVACY_OPTIONS
        ]
    if field == "category":
        return [
            InlineKeyboardButton(name, callback_data=f"setv:category:{cid}")
            for cid, name in YOUTUBE_CATEGORIES.items()
        ]
    return []


def _chunk_buttons(buttons: List[InlineKeyboardButton], per_row: int = 2):
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    data = query.data

    if data == "set:close":
        try:
            await query.edit_message_text("✅ 設定已關閉。")
        except Exception:  # noqa: BLE001
            pass
        return

    if data == "set:back":
        settings = load_settings()
        await query.edit_message_text(
            _overview_text(settings),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_overview_keyboard(),
        )
        return

    if data.startswith("set:"):
        field = data.split(":", 1)[1]
        options = _field_options(field)
        if not options:
            await query.edit_message_text("⚠️ 未知欄位。")
            return
        rows = _chunk_buttons(options, 2)
        rows.append([InlineKeyboardButton("↩️ 返回", callback_data="set:back")])
        await query.edit_message_text(
            f"請選擇新的「{field}」：",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("setv:"):
        _, field, value = data.split(":", 2)
        key_map = {
            "model": "default_model",
            "ratio": "default_ratio",
            "duration": "default_duration",
            "resolution": "default_resolution",
            "budget": "monthly_budget_usd",
            "privacy": "youtube_default_privacy",
            "category": "youtube_default_category",
        }
        setting_key = key_map.get(field)
        if setting_key is None:
            return
        try:
            typed_value: object = value
            if field == "duration":
                typed_value = int(value)
            elif field == "budget":
                typed_value = float(value)
        except ValueError:
            await query.edit_message_text("⚠️ 數值格式錯誤。")
            return
        settings = update_setting(setting_key, typed_value)
        await query.edit_message_text(
            _overview_text(settings),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_overview_keyboard(),
        )


async def budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    settings = load_settings()
    summary = cost_tracker.get_monthly_summary()
    budget = float(settings["monthly_budget_usd"])
    spent = float(summary["spent"])
    remaining = max(0.0, budget - spent)
    pct = 0.0 if budget <= 0 else min(100.0, spent / budget * 100)
    filled = int(round(pct / 6.25))  # 16 cells
    bar = "█" * filled + "░" * (16 - filled)
    text = (
        f"📊 *本月費用報告（{md2(summary['month'])}）*\n\n"
        f"已使用：${md2(f'{spent:.2f}')}\n"
        f"剩餘額度：${md2(f'{remaining:.2f}')}\n"
        f"生成次數：{md2(str(summary['generations']))} 次\n"
        f"月預算上限：${md2(f'{budget:.2f}')}\n\n"
        f"`{bar}` {md2(f'{pct:.1f}')}%"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    entries = cost_tracker.recent_entries(5)
    if not entries:
        await update.effective_message.reply_text("📭 尚未有生成記錄。")
        return
    lines = ["📜 *最近 5 筆生成記錄*", ""]
    for e in entries:
        ts = e.get("timestamp", "")
        try:
            ts_disp = datetime.fromisoformat(ts).strftime("%m-%d %H:%M")
        except ValueError:
            ts_disp = ts
        yt = e.get("youtube_url") or "未上傳"
        cost_str = f"{float(e.get('cost', 0)):.3f}"
        lines.append(
            f"• {md2(ts_disp)} \\| {md2(e.get('model', '?'))} \\| "
            f"{md2(e.get('mode', '?'))} \\| {md2(str(e.get('duration', '?')))}s \\| "
            f"${md2(cost_str)} \\| {md2(yt)}"
        )
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
    )


async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    args = context.args or []
    if args:
        code = " ".join(args).strip()
        try:
            google_auth.complete_auth(code)
        except Exception as exc:  # noqa: BLE001
            await update.effective_message.reply_text(
                f"❌ 授權失敗：{exc}"
            )
            return
        await update.effective_message.reply_text(
            "✅ Google 授權完成！Drive 與 YouTube 功能已啟用。"
        )
        return

    try:
        url = google_auth.build_auth_url()
    except Exception as exc:  # noqa: BLE001
        await update.effective_message.reply_text(f"❌ 無法建立授權 URL：{exc}")
        return
    await update.effective_message.reply_text(
        "🔐 請用瀏覽器打開以下 URL，登入並授權後，瀏覽器會跳轉到一個無法連線的 localhost 頁面——"
        "這是正常的。請從瀏覽器網址列複製完整的 URL（或只複製 code= 後面的值），然後傳給我：\n\n"
        f"{url}\n\n"
        "範例（貼完整 URL）：/auth http://localhost/?code=4/0Adxxxxxx\n"
        "範例（只貼 code）：/auth 4/0Adxxxxxx"
    )
