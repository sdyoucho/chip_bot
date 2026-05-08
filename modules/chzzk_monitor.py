"""
modules/chzzk_monitor.py
모쵸 — 치지직 방송 모니터링.

⚠️ 현재 R&D 보류 상태.
개쵸가 나중에 WebSocket 기반 실시간 모니터링을 재설계할 예정.
임시로 기본 응답만 반환.
"""

import logging

import discord

log = logging.getLogger(__name__)

# 해쵸가 참조하는 모니터링 버퍼 (비어있음)
_chat_buffers: dict = {}


async def get_current_status(streamer_name: str = "all") -> discord.Embed:
    """현재 방송 현황 — 모쵸 재설계 전까지 플레이스홀더."""
    embed = discord.Embed(
        title="📡 모쵸 — 방송 모니터링",
        description=(
            "⚠️ **모쵸 모듈은 현재 R&D 보류 상태입니다.**\n\n"
            "개쵸(`/ask 모쵸 개선 방안`)를 통해 WebSocket 기반 "
            "실시간 모니터링 시스템 개발이 진행될 예정입니다.\n\n"
            "현재는 Notion에 저장된 과거 데이터만 조회 가능합니다."
        ),
        color=0xEAB308,
    )
    embed.set_footer(text="재설계 예정 · 우선순위: 개쵸 작업 완료 후")
    return embed