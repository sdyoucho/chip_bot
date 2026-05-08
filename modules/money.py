"""
modules/money.py
인쵸 — 자금 현황 + 실시간 토큰 모니터링 + 월말정산.

기능:
1. 잔여 크레딧 조회 (OpenRouter /credits)
2. 임계치 알림 (50/70/100%)
3. 월별 지출 집계 + 다음 달 예상안
4. 모델별·에이전트별 비용 분해
"""

import asyncio
import logging
from datetime import datetime, timedelta

import discord

from utils.openrouter_client import get_remaining_credits
from utils.cost_tracker import (
    get_monthly_total, get_by_agent, get_by_model,
    get_daily_series, project_next_month,
)

log = logging.getLogger(__name__)

# 임계치 (0~1)
ALERT_THRESHOLDS = [0.5, 0.7, 1.0]
_alerted: set[float] = set()   # 이번 달 이미 알림 보낸 임계치

# 고정비 (서버·구독)
FIXED_COSTS_KRW = {
    "Railway Hobby": 6500,       # $5
    "Claude Code Max": 150000,
    "ChatGPT Plus": 30000,
}


async def get_financial_summary() -> discord.Embed:
    """현재 자금 현황 Embed."""
    credits = await get_remaining_credits()
    month_total = await get_monthly_total()
    by_agent = await get_by_agent()
    by_model = await get_by_model()

    usage_ratio = credits["usage_ratio"]
    bar = _progress_bar(usage_ratio)
    color = _color_by_ratio(usage_ratio)

    embed = discord.Embed(
        title="💰 인쵸 — 자금 현황",
        description=(
            f"**OpenRouter 크레딧**\n"
            f"{bar} `{usage_ratio*100:.1f}%`\n"
            f"사용: `${credits['usage']:.3f}` / 총 `${credits['total']:.3f}`\n"
            f"잔여: **`${credits['remaining']:.3f}`**"
        ),
        color=color,
    )

    # 이번 달 지출
    embed.add_field(
        name="📅 이번 달 누적 지출",
        value=f"**${month_total:.4f}** (≈ ₩{int(month_total * 1380):,})",
        inline=False,
    )

    # 에이전트별 TOP 5
    if by_agent:
        top = sorted(by_agent.items(), key=lambda x: -x[1])[:5]
        agent_lines = "\n".join(
            f"• {a}: `${c:.4f}`" for a, c in top
        )
        embed.add_field(name="🤖 에이전트별 (Top 5)", value=agent_lines, inline=True)

    # 모델별 TOP 5
    if by_model:
        top = sorted(by_model.items(), key=lambda x: -x[1])[:5]
        model_lines = "\n".join(
            f"• `{m.split('/')[-1][:25]}`: ${c:.4f}" for m, c in top
        )
        embed.add_field(name="🧠 모델별 (Top 5)", value=model_lines, inline=True)

    # 고정비
    fixed_sum = sum(FIXED_COSTS_KRW.values())
    embed.add_field(
        name="🏢 고정비 (월)",
        value=f"₩{fixed_sum:,}",
        inline=False,
    )

    embed.set_footer(text="임계치 50%/70%/100% 자동 알림 | /settlement 로 월말정산")
    return embed


# ── 임계치 모니터링 (스케줄러에서 15분마다 호출) ───────────────────
async def check_thresholds(bot: discord.Client) -> None:
    """크레딧 사용률이 임계치를 넘으면 Cho에게 DM."""
    import os
    credits = await get_remaining_credits()
    ratio = credits["usage_ratio"]

    for threshold in ALERT_THRESHOLDS:
        if ratio >= threshold and threshold not in _alerted:
            _alerted.add(threshold)
            await _send_threshold_alert(bot, threshold, credits)


async def _send_threshold_alert(bot, threshold: float, credits: dict) -> None:
    import os
    cho_id = int(os.getenv("CHO_USER_ID", "0"))
    if not cho_id:
        return

    emoji = {0.5: "🟡", 0.7: "🟠", 1.0: "🔴"}[threshold]
    title = f"{emoji} 인쵸 — 크레딧 {int(threshold*100)}% 도달"
    color = {0.5: 0xEAB308, 0.7: 0xF97316, 1.0: 0xDC2626}[threshold]

    try:
        user = await bot.fetch_user(cho_id)
        embed = discord.Embed(
            title=title,
            description=(
                f"OpenRouter 크레딧 사용률이 **{int(threshold*100)}%**를 넘었습니다.\n\n"
                f"사용: `${credits['usage']:.3f}` / 총 `${credits['total']:.3f}`\n"
                f"잔여: `${credits['remaining']:.3f}`"
            ),
            color=color,
        )
        if threshold >= 1.0:
            embed.add_field(
                name="⚠️ 조치 필요",
                value="크레딧 충전 또는 서비스 일시 중단 권고",
                inline=False,
            )
        await user.send(embed=embed)
        log.warning(f"임계치 알림 발송: {threshold*100}%")
    except Exception as e:
        log.error(f"임계치 알림 실패: {e}")


# ── 월말정산 (매월 말일 23:00에 스케줄러가 호출) ───────────────────
async def monthly_settlement() -> discord.Embed:
    """이번 달 결산 + 다음 달 예상."""
    month_total = await get_monthly_total()
    by_agent = await get_by_agent()
    by_model = await get_by_model()
    daily = await get_daily_series()
    projection = await project_next_month()

    now = datetime.now()
    embed = discord.Embed(
        title=f"📊 인쵸 — {now.year}년 {now.month}월 월말정산",
        color=0x0EA5E9,
    )

    # 총 지출
    krw = int(month_total * 1380)
    embed.add_field(
        name="💸 이번 달 AI 토큰 총 지출",
        value=f"**${month_total:.4f}** (≈ ₩{krw:,})",
        inline=False,
    )

    # 에이전트 분해
    if by_agent:
        lines = "\n".join(
            f"• **{a}**: ${c:.4f} ({c/month_total*100:.1f}%)"
            for a, c in sorted(by_agent.items(), key=lambda x: -x[1])
        )
        embed.add_field(name="🤖 에이전트별 분해", value=lines, inline=False)

    # 일별 추이 (텍스트 스파크라인)
    if daily:
        spark = _sparkline([d["cost"] for d in daily])
        embed.add_field(
            name=f"📈 일별 추이 ({len(daily)}일)",
            value=f"`{spark}`\n최대: ${max(d['cost'] for d in daily):.4f}/일",
            inline=False,
        )

    # 다음 달 예상
    fixed_sum = sum(FIXED_COSTS_KRW.values())
    total_next = int(projection * 1380) + fixed_sum
    embed.add_field(
        name="🔮 다음 달 예상 유지비",
        value=(
            f"AI 토큰 예상: ${projection:.4f} (≈ ₩{int(projection*1380):,})\n"
            f"고정비: ₩{fixed_sum:,}\n"
            f"**합계: ₩{total_next:,}**"
        ),
        inline=False,
    )

    embed.set_footer(text=f"매월 말일 23시 자동 생성 | 생성: {now:%Y-%m-%d %H:%M}")

    # 임계치 카운터 리셋
    _alerted.clear()
    return embed


# ── 보조 함수 ──────────────────────────────────────────────────────
def _progress_bar(ratio: float, length: int = 20) -> str:
    filled = int(ratio * length)
    return "█" * filled + "░" * (length - filled)


def _color_by_ratio(ratio: float) -> int:
    if ratio >= 1.0: return 0xDC2626
    if ratio >= 0.7: return 0xF97316
    if ratio >= 0.5: return 0xEAB308
    return 0x059669


def _sparkline(values: list[float]) -> str:
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    return "".join(blocks[int((v - lo) / span * 7)] for v in values)