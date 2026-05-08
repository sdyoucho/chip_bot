"""modules/design.py — 디쵸 — 디자인 제안. OpenRouter vision 티어 (gpt-4o)."""

import logging
import discord

from utils.openrouter_client import chat

log = logging.getLogger(__name__)

SYSTEM = (
    "당신은 '디쵸'입니다. 스트리머 채널 디자인·포스터·PPT·썸네일 레퍼런스와 "
    "아이디어를 제안합니다. Figma 기반 디자인 구성을 제안할 수 있습니다."
)


async def handle_query(query: str) -> discord.Embed:
    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": query},
            ],
            agent="dichyo",
            max_tokens=800,
            temperature=0.8,
        )
        embed = discord.Embed(
            title="🎨 디쵸 — 디자인 제안",
            description=result["content"][:3500],
            color=0xDB2777,
        )
        embed.set_footer(
            text=f"{result['model'].split('/')[-1]} · ${result['cost']:.5f}"
        )
        return embed
    except Exception as e:
        from bot.embeds import embed_error
        return embed_error("디자인 오류", str(e))