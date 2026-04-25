"""Shared AsyncOpenAI client singleton."""
from __future__ import annotations

from typing import Optional

from openai import AsyncOpenAI

from config import OPENAI_API_KEY

_client: Optional[AsyncOpenAI] = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _client
