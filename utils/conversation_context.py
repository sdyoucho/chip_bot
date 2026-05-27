"""
utils/conversation_context.py
Discord 답변(Reply) 기능을 활용한 대화 맥락 수집.

Discord에서 메시지에 "답장"하면 reference 필드로 원본 메시지를 가리킴.
이를 추적하여 이전 대화 내용을 컨텍스트로 활용.
"""

import logging
from typing import Optional

import discord

log = logging.getLogger(__name__)

MAX_CONTEXT_MESSAGES = 5     # 최대 N개의 이전 메시지 추적
MAX_MESSAGE_LENGTH = 2000     # 메시지당 최대 길이


async def get_reply_context(
    interaction: discord.Interaction,
    *,
    max_depth: int = MAX_CONTEXT_MESSAGES,
) -> list[dict]:
    """
    Interaction이 어떤 메시지에 대한 답글인지 추적하여 컨텍스트 반환.

    Discord에서 사용자가 봇 메시지에 답변(Reply)하면서 슬래시 커맨드를 사용하면,
    interaction 자체로는 reference를 알 수 없지만 채널 최근 메시지 중 reply를 찾을 수 있음.

    Returns:
        [
            {"author": "Cho", "content": "...", "timestamp": "...", "is_bot": False},
            {"author": "head cho", "content": "...", "timestamp": "...", "is_bot": True},
            ...
        ]
        최신 → 오래된 순서
    """
    context = []

    try:
        channel = interaction.channel
        if channel is None:
            return context

        # 최근 메시지 N개 가져오기
        async for msg in channel.history(limit=20):
            if len(context) >= max_depth:
                break

            # 봇 자신의 메시지나 사용자 메시지 모두 포함
            content = msg.content or ""
            if msg.embeds:
                # Embed의 description도 본문으로 취급
                for emb in msg.embeds:
                    if emb.description:
                        content += "\n" + emb.description[:1000]
                    if emb.title:
                        content = f"[{emb.title}]\n{content}"

            if not content.strip():
                continue

            context.append({
                "author": msg.author.display_name,
                "content": content[:MAX_MESSAGE_LENGTH],
                "timestamp": msg.created_at.isoformat(),
                "is_bot": msg.author.bot,
                "message_id": msg.id,
            })

        # 최신 → 오래된 순으로 정렬되어 있음
        return context

    except discord.Forbidden:
        log.warning("채널 메시지 읽기 권한 없음 — Read Message History 권한 필요")
        return []
    except Exception as e:
        log.warning(f"컨텍스트 수집 실패: {e}")
        return []


async def get_referenced_message(
    interaction: discord.Interaction,
) -> Optional[discord.Message]:
    """
    interaction의 채널에서 가장 최근에 봇이 보낸 메시지를 찾음.
    (사용자가 "방금 봤던 그 내용" 같이 언급할 때 활용)
    """
    try:
        channel = interaction.channel
        if channel is None:
            return None

        async for msg in channel.history(limit=10):
            if msg.author.id == interaction.client.user.id:
                return msg
        return None
    except Exception as e:
        log.warning(f"최근 봇 메시지 조회 실패: {e}")
        return None


def format_context_for_prompt(context: list[dict]) -> str:
    """수집된 컨텍스트를 LLM 프롬프트 형식으로 변환."""
    if not context:
        return ""

    lines = ["--- 이전 대화 맥락 (최근 → 오래된 순) ---\n"]

    for i, msg in enumerate(reversed(context), 1):  # 오래된 → 최신 순서로 출력
        role = "🤖 봇" if msg["is_bot"] else "👤 사용자"
        lines.append(f"\n[{i}] {role} ({msg['author']})")
        lines.append(msg["content"])
        lines.append("")

    return "\n".join(lines)


def detect_context_reference(query: str) -> bool:
    """
    쿼리에 "이전 내용", "그거", "위에서" 같은 참조 표현이 있는지 감지.
    있으면 자동으로 컨텍스트를 추가하도록 신호 반환.
    """
    reference_patterns = [
        "이전", "방금", "위에서", "그거", "그건", "그 내용",
        "조금 전", "아까", "앞서", "앞에서", "직전",
        "previous", "above", "earlier", "that",
    ]
    query_lower = query.lower()
    return any(pattern in query_lower for pattern in reference_patterns)