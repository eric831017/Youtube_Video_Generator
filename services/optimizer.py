"""GPT-4o prompt optimizer with optional vision input."""
from __future__ import annotations

from typing import Optional

from openai import AsyncOpenAI

from config import OPENAI_API_KEY, logger

SYSTEM_T2V = (
    "You are a cinematic AI video prompt expert.\n"
    "Rewrite the user's prompt to be more detailed and effective for AI video "
    "generation. Include camera movement, lighting, mood, and style details.\n"
    "Keep it under 200 words. Return only the optimized prompt, nothing else."
)

SYSTEM_I2V = (
    "You are a cinematic AI video prompt expert.\n"
    "The user provided an image that will be used as the FIRST FRAME of the "
    "video. The AI model will animate this exact image.\n"
    "Optimize the prompt to describe ONLY the motion, camera movement, and "
    "dynamics that should occur starting from this image.\n"
    "Do NOT describe the scene content — the image already defines it.\n"
    "Keep it under 150 words. Return only the optimized prompt."
)

SYSTEM_REF = (
    "You are a cinematic AI video prompt expert.\n"
    "The user provided a reference image for visual style guidance.\n"
    "Analyze the image's color palette, lighting, mood, and visual style, "
    "then incorporate these elements into an optimized video generation prompt "
    "based on the user's text input.\n"
    "Keep it under 200 words. Return only the optimized prompt."
)


_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _client


async def optimize_prompt(
    raw_prompt: str,
    image_mode: Optional[str] = None,
    image_data: Optional[str] = None,
    image_mime: Optional[str] = None,
) -> str:
    """Return an optimized video prompt. Falls back to raw prompt on failure."""
    if image_mode == "i2v":
        system = SYSTEM_I2V
    elif image_mode == "ref":
        system = SYSTEM_REF
    else:
        system = SYSTEM_T2V

    if image_mode in ("i2v", "ref") and image_data and image_mime:
        user_content = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image_mime};base64,{image_data}"},
            },
            {"type": "text", "text": raw_prompt},
        ]
    else:
        user_content = raw_prompt

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

    client = _get_client()
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.7,
        max_tokens=500,
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        logger.warning("Optimizer returned empty string; using raw prompt")
        return raw_prompt
    return text
