"""modules/rnd.py — 개쵸 — R&D/기술 문의. OpenRouter standard 티어."""

import logging
import discord

from utils.openrouter_client import chat

log = logging.getLogger(__name__)

SYSTEM = (
    "당신은 '개쵸'입니다. Python, Discord.py, Notion API, YouTube API, "
    "스트리밍 플랫폼 연동, Railway 배포에 특화된 개발 전문가로서 "
    "Cho의 매니지먼트 봇 시스템 유지보수 및 신규 봇 개발 문의에 답변합니다."
)


async def handle_query(query: str) -> discord.Embed:
    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": query},
            ],
            agent="gaechyo",
            max_tokens=1000,
            temperature=0.5,
        )
        embed = discord.Embed(
            title="🔧 개쵸 — R&D",
            description=result["content"][:3500],
            color=0x06B6D4,
        )
        embed.set_footer(
            text=f"{result['model'].split('/')[-1]} · ${result['cost']:.5f}"
        )
        return embed
    except Exception as e:
        from bot.embeds import embed_error
        return embed_error("R&D 오류", str(e))