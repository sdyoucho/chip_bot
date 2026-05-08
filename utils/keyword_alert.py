"""
utils/keyword_alert.py
키워드 실시간 감지 → Discord DM 발송.
LLM 없이 Python 문자열 매칭 — 비용 ₩0.
"""

import logging
import os
from dataclasses import dataclass

import discord

log = logging.getLogger(__name__)

# ── 기본 모니터링 키워드 ──────────────────────────────────────────────
DEFAULT_KEYWORDS = {
    "긴급": ["사고", "논란", "사과", "방송사고", "욕설", "차단", "신고"],
    "급상승": [],     # viewer_tracker에서 자동 감지
    "긍정": ["대박", "gg", "개웃", "ㅋㅋㅋ", "레전드", "미쳤다"],
    "후원": ["별풍선", "구독", "슈퍼챗", "도네"],
}

# 실행 중 동적으로 추가된 키워드
_custom_keywords: dict[str, list[str]] = {}


def add_keyword(category: str, keyword: str):
    """커스텀 키워드 추가."""
    if category not in _custom_keywords:
        _custom_keywords[category] = []
    _custom_keywords[category].append(keyword.lower())
    log.info(f"키워드 추가: [{category}] {keyword}")


def remove_keyword(category: str, keyword: str):
    if category in _custom_keywords:
        _custom_keywords[category] = [k for k in _custom_keywords[category] if k != keyword.lower()]


@dataclass
class AlertResult:
    triggered: bool
    category: str
    matched_keyword: str
    message: str


def check_keywords(text: str, streamer_name: str = "") -> list[AlertResult]:
    """
    채팅 메시지에서 키워드 감지.
    반환: 매칭된 AlertResult 리스트.
    """
    text_lower = text.lower()
    results = []

    all_keywords = {**DEFAULT_KEYWORDS, **_custom_keywords}
    for category, keywords in all_keywords.items():
        for kw in keywords:
            if kw and kw in text_lower:
                results.append(AlertResult(
                    triggered=True,
                    category=category,
                    matched_keyword=kw,
                    message=f"[{streamer_name}] **{category}** 키워드 감지: `{kw}`\n원문: {text[:100]}",
                ))
                break  # 카테고리당 첫 번째 매칭만

    return results


async def send_alert(bot: discord.Client, alert: AlertResult):
    """Cho에게 Discord DM으로 알림 발송."""
    cho_id = int(os.getenv("CHO_USER_ID", "0"))
    if not cho_id:
        log.warning("CHO_USER_ID 미설정 — 알림 발송 불가")
        return

    try:
        user = await bot.fetch_user(cho_id)
        color = 0xE11D48 if alert.category == "긴급" else 0xD97706
        embed = discord.Embed(
            title=f"🔔 알림 — {alert.category}",
            description=alert.message,
            color=color,
        )
        await user.send(embed=embed)
        log.info(f"알림 발송: {alert.category} / {alert.matched_keyword}")
    except Exception as e:
        log.error(f"알림 발송 실패: {e}")


async def process_chat_message(bot: discord.Client, text: str, streamer_name: str):
    """채팅 한 줄 처리 — 키워드 감지 후 필요 시 알림."""
    alerts = check_keywords(text, streamer_name)
    for alert in alerts:
        await send_alert(bot, alert)
