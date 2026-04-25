"""Multi-shot storyboard: intent classification, generation, revision, confirmation."""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import MODELS, load_settings, logger
from handlers.common import get_session, md2, reject_unauthorized
from models.cost_tracker import get_monthly_spent
from models.session import State, UserSession
from services.openai_client import get_client

_CLASSIFY_SYSTEM = (
    "Classify whether the user input describes a single video shot "
    "(one scene, one action) or a story/sequence requiring multiple shots "
    "(narrative arc, multiple scenes, or a theme that unfolds over time).\n"
    "Reply with ONLY one of: single_shot  multi_shot"
)

_STORYBOARD_SYSTEM_TEMPLATE = """You are a video storyboard director.
Break the user's story/theme into exactly 2-3 shots.
Each shot must be {max_dur} seconds or less.
Total duration must not exceed 30 seconds.

Rules:
- style_anchor: detailed appearance of the main subject/character \
(reused verbatim in every shot for visual consistency)
- scene_anchor: environment, location, lighting atmosphere (reused across shots)
- Each shot prompt: describe ONLY the action + camera movement for that shot. \
Do NOT repeat style_anchor or scene_anchor.
- transition_note: how the shot should end to allow smooth chaining to the next \
shot (e.g. "end on close-up of subject's face looking right"). \
For the last shot, write "final shot".

Return ONLY valid JSON, no markdown fences, no explanation:
{{
  "style_anchor": "...",
  "scene_anchor": "...",
  "shots": [
    {{"id": 1, "duration": 8, "prompt": "...", "transition_note": "..."}},
    {{"id": 2, "duration": 8, "prompt": "...", "transition_note": "..."}},
    {{"id": 3, "duration": 8, "prompt": "...", "transition_note": "..."}}
  ],
  "total_duration": 24
}}"""

_REVISE_SYSTEM = (
    "Revise this video storyboard JSON based on the user's feedback. "
    "Keep the same JSON structure and field names exactly. "
    "Return ONLY valid JSON, no markdown, no explanation."
)


async def classify_intent(raw_prompt: str) -> str:
    """Return 'single_shot' or 'multi_shot'."""
    client = get_client()
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": _CLASSIFY_SYSTEM},
            {"role": "user", "content": raw_prompt},
        ],
        max_tokens=5,
        temperature=0,
    )
    result = (response.choices[0].message.content or "").strip()
    return "multi_shot" if "multi" in result else "single_shot"


def _strip_fences(text: str) -> str:
    return text.replace("```json", "").replace("```", "").strip()


async def generate_storyboard(raw_prompt: str, model_key: str) -> dict:
    max_dur = MODELS[model_key]["max_duration"]
    system = _STORYBOARD_SYSTEM_TEMPLATE.format(max_dur=max_dur)
    client = get_client()
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": raw_prompt},
        ],
        max_tokens=800,
        temperature=0.7,
    )
    raw = _strip_fences(response.choices[0].message.content or "")
    return json.loads(raw)


async def revise_storyboard(original: dict, revision_request: str) -> dict:
    client = get_client()
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": _REVISE_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Original storyboard:\n{json.dumps(original, indent=2, ensure_ascii=False)}"
                    f"\n\nRevision request:\n{revision_request}"
                ),
            },
        ],
        max_tokens=800,
        temperature=0.7,
    )
    raw = _strip_fences(response.choices[0].message.content or "")
    return json.loads(raw)


def format_storyboard_message(session: UserSession, settings: dict) -> str:
    model_cfg = MODELS[settings["default_model"]]
    label = model_cfg["label"]
    price = float(model_cfg["price_per_sec"])
    total_cost = sum(float(s["duration"]) * price for s in session.storyboard)

    lines = [
        f"🎬 *分鏡腳本（共 {len(session.storyboard)} 段，約 {session.total_duration} 秒）*",
        "",
        f"🎨 *風格：* {md2(session.style_anchor or '')}",
        f"🌍 *場景：* {md2(session.scene_anchor or '')}",
        "",
    ]
    for shot in session.storyboard:
        chain = "（接上段末幀）" if shot["id"] > 1 else "（文字生成）"
        lines += [
            f"*第 {shot['id']} 段｜{shot['duration']} 秒* {chain}",
            md2(shot["prompt"]),
            f"_{md2(shot['transition_note'])}_",
            "",
        ]
    lines += [
        f"💰 *預估總費用：* ${md2(f'{total_cost:.3f}')}",
        f"🤖 *模型：* {md2(label)}",
        "",
        md2("如需修改參數請 /cancel 後使用 /settings 調整。"),
    ]
    return "\n".join(lines)


def _storyboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 確認生成", callback_data="storyboard:confirm"),
            InlineKeyboardButton("✏️ 修改分鏡", callback_data="storyboard:revise"),
        ],
        [InlineKeyboardButton("❌ 取消", callback_data="storyboard:cancel")],
    ])


async def run_storyboarding_flow(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    session: UserSession,
    raw_prompt: str,
    revision_text: Optional[str] = None,
    progress_message: Optional[Message] = None,
) -> None:
    """Generate or revise a storyboard and send confirmation message."""
    settings = load_settings()
    model_key = settings["default_model"]

    async def _send_error(text: str) -> None:
        if progress_message:
            try:
                await progress_message.edit_text(text)
                return
            except Exception:  # noqa: BLE001
                pass
        await context.bot.send_message(chat_id, text)

    try:
        if revision_text and session.storyboard:
            original = {
                "style_anchor": session.style_anchor,
                "scene_anchor": session.scene_anchor,
                "shots": session.storyboard,
                "total_duration": session.total_duration,
            }
            for attempt in range(2):
                try:
                    parsed = await revise_storyboard(original, revision_text)
                    break
                except (json.JSONDecodeError, KeyError):
                    if attempt == 1:
                        await _send_error("❌ 分鏡修改失敗，請重新嘗試。")
                        session.reset()
                        return
        else:
            for attempt in range(2):
                try:
                    parsed = await generate_storyboard(raw_prompt, model_key)
                    break
                except (json.JSONDecodeError, KeyError):
                    if attempt == 1:
                        await _send_error("❌ 分鏡生成失敗，請重新嘗試。")
                        session.reset()
                        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Storyboard generation/revision error")
        await _send_error(f"❌ 分鏡生成失敗：{exc}")
        session.reset()
        return

    session.storyboard = parsed["shots"]
    session.style_anchor = parsed.get("style_anchor", "")
    session.scene_anchor = parsed.get("scene_anchor", "")
    session.total_duration = parsed.get(
        "total_duration",
        sum(int(s.get("duration", 8)) for s in parsed["shots"]),
    )
    session.state = State.AWAITING_STORYBOARD_CONFIRM

    body = format_storyboard_message(session, settings)
    keyboard = _storyboard_keyboard()

    if progress_message:
        try:
            await progress_message.edit_text(
                body, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard
            )
            return
        except Exception:  # noqa: BLE001
            pass
    await context.bot.send_message(
        chat_id, body, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard
    )


async def handle_storyboard_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
    chat_id = query.message.chat_id

    if session.state != State.AWAITING_STORYBOARD_CONFIRM:
        await query.edit_message_text("⚠️ 此確認按鈕已過期，請重新開始。")
        return

    action = query.data.split(":", 1)[1]

    if action == "cancel":
        session.reset()
        await query.edit_message_text("❌ 已取消分鏡生成。")
        return

    if action == "revise":
        session.state = State.AWAITING_STORYBOARD_REVISION
        await query.edit_message_text(
            "✏️ 請輸入修改意見（例如：「把第二段改成夜景，主角換成機器人」）："
        )
        return

    if action == "confirm":
        settings = load_settings()
        model_cfg = MODELS.get(settings["default_model"])
        if not model_cfg:
            await query.edit_message_text("❌ 模型設定錯誤，請 /settings 重新選擇。")
            session.reset()
            return

        total_cost = sum(
            float(s["duration"]) * float(model_cfg["price_per_sec"])
            for s in session.storyboard
        )
        session.estimated_cost = total_cost

        monthly_spent = get_monthly_spent()
        budget = float(settings["monthly_budget_usd"])
        if monthly_spent + total_cost > budget:
            await query.edit_message_text(
                f"⚠️ 本月預算不足（已用 ${monthly_spent:.2f} "
                f"+ 預估 ${total_cost:.3f} > 上限 ${budget:.2f}）。\n"
                "請用 /settings 調整預算上限。"
            )
            session.reset()
            return

        session.state = State.GENERATING_MULTI
        await query.edit_message_text(
            f"✅ 已確認！開始生成 {len(session.storyboard)} 段影片..."
        )

        from handlers.generate import generate_multi_shot_task  # avoid circular at module level
        task = asyncio.create_task(
            generate_multi_shot_task(context, chat_id, user.id)
        )
        session.polling_task = task
