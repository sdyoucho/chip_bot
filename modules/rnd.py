"""modules/rnd.py — R&D / 기술 문의 — Claude Sonnet."""

import logging, os
import anthropic, discord
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

SYSTEM = (
    "당신은 Python, Discord.py, Notion API, YouTube API, 스트리밍 플랫폼 연동에 "
    "특화된 개발 전문가입니다. Cho의 매니지먼트 봇 시스템 관련 기술 문의에 답변합니다."
)


async def handle_query(query: str) -> discord.Embed:
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    try:
        msg = await client.messages.create(
            model="claude-sonnet-4-6", max_tokens=800,
            system=SYSTEM,
            messages=[{"role": "user", "content": query}],
        )
        embed = discord.Embed(
            title="🔧 R&D",
            description=msg.content[0].text[:2000],
            color=0x059669,
        )
        embed.set_footer(text="Claude Sonnet | base5 R&D 모듈")
        return embed
    except Exception as e:
        from bot.embeds import embed_error
        return embed_error("R&D 오류", str(e))
