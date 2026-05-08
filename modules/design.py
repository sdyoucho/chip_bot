"""modules/design.py — 디자인 레퍼런스 — GPT-4o."""

import logging, os
import openai, discord
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)


async def handle_query(query: str) -> discord.Embed:
    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        res = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "당신은 콘텐츠 디자인 전문가입니다. 스트리머 채널 디자인 레퍼런스와 아이디어를 제안합니다."},
                {"role": "user", "content": query},
            ],
            max_tokens=600,
        )
        content = res.choices[0].message.content
        embed = discord.Embed(title="🎨 디자인 제안", description=content[:2000], color=0xDB2777)
        return embed
    except Exception as e:
        from bot.embeds import embed_error
        return embed_error("디자인 오류", str(e))
