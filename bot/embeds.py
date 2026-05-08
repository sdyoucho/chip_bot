"""
bot/embeds.py
Discord Embed 포맷터 모음.
"""

import discord


def embed_error(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=f"❌ {title}", description=description, color=0xE11D48)


def embed_info(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=0x4F46E5)


def embed_success(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=f"✅ {title}", description=description, color=0x059669)


def embed_thinking(title: str = "처리 중...") -> discord.Embed:
    return discord.Embed(title=f"⏳ {title}", color=0x94A3B8)


def embed_unknown_command(query: str) -> discord.Embed:
    e = discord.Embed(
        title="❓ 명령을 이해하지 못했어요",
        description=f"입력: `{query}`\n\n`/help`로 사용 가능한 명령을 확인하거나 더 구체적으로 입력해주세요.",
        color=0xD97706,
    )
    return e


def embed_report(
    streamer_name: str,
    period: str,
    broadcast_summary: str,
    youtube_summary: str,
    competitor_summary: str,
    suggestion: str,
) -> discord.Embed:
    """주간 리포트 전용 Embed."""
    e = discord.Embed(
        title=f"📊 주간 리포트 — {streamer_name}",
        description=f"기간: {period}",
        color=0x4F46E5,
    )
    if broadcast_summary:
        e.add_field(name="🎙️ 방송 현황", value=broadcast_summary, inline=False)
    if youtube_summary:
        e.add_field(name="📺 유튜브 성과", value=youtube_summary, inline=False)
    if competitor_summary:
        e.add_field(name="🔍 경쟁 채널", value=competitor_summary, inline=False)
    if suggestion:
        e.add_field(name="✨ AI 개선 제안", value=suggestion, inline=False)
    e.set_footer(text="Cho's 매니지먼트 봇 | 자동 생성")
    return e
