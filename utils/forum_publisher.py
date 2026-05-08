"""
utils/forum_publisher.py
해쵸 요청 1건 = Forum Thread 1개.
- raw 메시지(각 agent 결과) + summary 메시지(해쵸 종합)를 병렬 발행
- 페르소나별 Webhook으로 발화
"""

import asyncio
import logging
import os
from datetime import datetime

import discord

from utils.persona import speak

log = logging.getLogger(__name__)


async def publish_session(
    bot: discord.Client,
    *,
    query: str,
    agent_results: dict[str, tuple[discord.Embed, str]],  # {agent: (embed, raw_text)}
    summary_embed: discord.Embed,
) -> discord.Thread | None:
    """
    포럼 채널에 새 스레드 생성 후 결과 병렬 발행.
    agent_results: {"gihyo": (embed, "원문 JSON/텍스트"), ...}
    """
    forum_id = os.getenv("FORUM_CHANNEL_ID", "").strip()
    if not forum_id.isdigit():
        log.info("FORUM_CHANNEL_ID 미설정 — 포럼 발행 생략")
        return None

    forum: discord.ForumChannel = bot.get_channel(int(forum_id))  # type: ignore
    if not isinstance(forum, discord.ForumChannel):
        log.warning("FORUM_CHANNEL_ID가 Forum 채널이 아님")
        return None

    # 스레드 생성 (해쵸 요약을 초기 메시지로)
    title = f"[{datetime.now():%m-%d %H:%M}] {query[:60]}"
    thread_with_msg = await forum.create_thread(
        name=title,
        embed=summary_embed,
    )
    thread = thread_with_msg.thread

    # 각 agent raw 결과를 병렬로 발행
    tasks = []
    for agent, (embed, raw_text) in agent_results.items():
        tasks.append(speak(thread, agent, embed=embed, thread=thread))
        # raw 원문이 길면 파일로 첨부
        if raw_text and len(raw_text) > 1500:
            file = discord.File(
                fp=_make_file(raw_text),
                filename=f"{agent}_raw.txt",
            )
            tasks.append(thread.send(f"📎 **{agent}** 원본 데이터", file=file))

    await asyncio.gather(*tasks, return_exceptions=True)
    log.info(f"포럼 발행 완료: {thread.id} ({len(agent_results)} agents)")
    return thread


def _make_file(text: str):
    import io
    return io.BytesIO(text.encode("utf-8"))