"""
utils/persona.py
에이전트별 캐릭터 정의 + Webhook을 이용한 발화.
각 agent가 자신의 이름·아바타·색상으로 메시지 전송.
"""

import logging
import os
from dataclasses import dataclass

import aiohttp
import discord

log = logging.getLogger(__name__)


@dataclass
class Persona:
    key: str
    name: str                # 표시 이름 (Webhook username)
    emoji: str
    color: int
    avatar_url: str          # 각 페르소나 아바타 이미지 URL
    description: str


PERSONAS: dict[str, Persona] = {
    "haecho":  Persona("haecho",  "🎯 해쵸", "🎯", 0x1E293B,
                       os.getenv("AVATAR_HAECHO", ""),  "총괄 브리핑"),
    "gihyo":   Persona("gihyo",   "📋 기쵸", "📋", 0x4F46E5,
                       os.getenv("AVATAR_GIHYO", ""),   "기획·협업 제안"),
    "inchyo":  Persona("inchyo",  "💰 인쵸", "💰", 0x059669,
                       os.getenv("AVATAR_INCHYO", ""),  "자금·토큰 모니터링"),
    "bunchyo": Persona("bunchyo", "🔍 분쵸", "🔍", 0x7C3AED,
                       os.getenv("AVATAR_BUNCHYO", ""), "분석·리서치"),
    "sochyo":  Persona("sochyo",  "📅 스쵸", "📅", 0x0EA5E9,
                       os.getenv("AVATAR_SOCHYO", ""),  "스케줄"),
    "mochyo":  Persona("mochyo",  "📡 모쵸", "📡", 0xEAB308,
                       os.getenv("AVATAR_MOCHYO", ""),  "방송 모니터링"),
    "gaechyo": Persona("gaechyo", "🔧 개쵸", "🔧", 0x06B6D4,
                       os.getenv("AVATAR_GAECHYO", ""), "R&D·봇 개발"),
    "dichyo":  Persona("dichyo",  "🎨 디쵸", "🎨", 0xDB2777,
                       os.getenv("AVATAR_DICHYO", ""),  "Figma 디자인"),
}


# ── Webhook 캐시 (채널별 재사용) ─────────────────────────────────────
_webhook_cache: dict[int, discord.Webhook] = {}


async def _get_or_create_webhook(channel: discord.TextChannel) -> discord.Webhook:
    if channel.id in _webhook_cache:
        return _webhook_cache[channel.id]

    hooks = await channel.webhooks()
    hook = next((h for h in hooks if h.name == "ChoMgmtAgents"), None)
    if not hook:
        hook = await channel.create_webhook(name="ChoMgmtAgents")
    _webhook_cache[channel.id] = hook
    return hook


async def speak(
    channel: discord.abc.Messageable,
    agent: str,
    *,
    content: str = "",
    embed: discord.Embed | None = None,
    thread: discord.Thread | None = None,
) -> None:
    """
    agent의 페르소나로 채널/스레드에 메시지 발송.
    Webhook 지원 채널(TextChannel/ForumChannel 스레드)이면 Webhook 사용,
    아니면 Embed author 필드로 폴백.
    """
    persona = PERSONAS.get(agent)
    if not persona:
        log.warning(f"알 수 없는 agent: {agent}")
        return

    # Webhook 경로 (TextChannel 또는 스레드의 parent가 TextChannel/ForumChannel)
    target_channel = thread.parent if thread else channel
    if isinstance(target_channel, (discord.TextChannel, discord.ForumChannel)):
        try:
            hook = await _get_or_create_webhook(target_channel)
            kwargs = {
                "username": persona.name,
                "avatar_url": persona.avatar_url or None,
                "content": content or None,
            }
            if embed:
                # embed 색상·author 자동 세팅
                embed.color = persona.color
                if not embed.author:
                    embed.set_author(name=persona.name, icon_url=persona.avatar_url or None)
                kwargs["embed"] = embed
            if thread:
                kwargs["thread"] = thread
            await hook.send(**kwargs)
            return
        except Exception as e:
            log.warning(f"Webhook 전송 실패 ({agent}): {e} → Embed 폴백")

    # 폴백: 일반 send + Embed author
    if embed:
        embed.color = persona.color
        embed.set_author(name=persona.name, icon_url=persona.avatar_url or None)
        await channel.send(embed=embed)
    elif content:
        await channel.send(f"**{persona.name}**\n{content}")