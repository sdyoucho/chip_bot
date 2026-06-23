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
from utils.credit_config import (
    get_monthly_limit, get_thresholds, is_alerted, mark_alerted,
)

log = logging.getLogger(__name__)


async def get_financial_summary() -> discord.Embed:
    """현재 자금 현황 Embed."""
    credits = await get_remaining_credits()
    month_total = await get_monthly_total()
    by_agent = await get_by_agent()
    by_model = await get_by_model()

    monthly_limit = get_monthly_limit()
    month_ratio = month_total / monthly_limit if monthly_limit else 0
    bar = _progress_bar(month_ratio)
    color = _color_by_ratio(month_ratio)

    embed = discord.Embed(
        title="💰 인쵸 — 자금 현황",
        description=(
            f"**이번 달 크레딧 한도**\n"
            f"{bar} `{month_ratio*100:.1f}%`\n"
            f"사용: `${month_total:.3f}` / 한도 `${monthly_limit:.2f}`\n"
            f"잔여: **`${monthly_limit - month_total:.3f}`**"
        ),
        color=color,
    )

    # OpenRouter 계정 전체 잔여 크레딧 (참고용 — 알림 기준 아님)
    embed.add_field(
        name="🌐 OpenRouter 계정 잔여",
        value=(
            f"사용: `${credits['usage']:.3f}` / 총 `${credits['total']:.3f}` "
            f"(`{credits['usage_ratio']*100:.1f}%`)\n"
            f"잔여: `${credits['remaining']:.3f}`"
        ),
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

    # 고정비 (편집: /fixedcost_add 등)
    from modules.fixed_costs import get_total_monthly_krw
    fixed_sum = await get_total_monthly_krw()
    embed.add_field(
        name="🏢 고정비 (월)",
        value=f"₩{fixed_sum:,}",
        inline=False,
    )

    thresholds_label = "/".join(f"{int(t*100)}%" for t in get_thresholds())
    embed.set_footer(text=f"임계치 {thresholds_label} 자동 알림 | /settlement 로 월말정산")
    return embed


# ── 임계치 모니터링 (스케줄러에서 15분마다 호출) ───────────────────
async def check_thresholds(bot: discord.Client) -> None:
    """이번 달 크레딧 사용률(월 한도 기준)이 임계치를 넘으면 Cho에게 DM.

    OpenRouter 계정의 전체(누적) 크레딧이 아니라, 이번 달 실사용액을
    월 한도로 나눈 비율을 기준으로 한다. 발송 여부는 "YYYY-MM" 단위로
    파일에 영속화되어, 봇이 재시작돼도 같은 달에 같은 임계치를 다시
    보내지 않는다 (재시작 시 50%/70%가 동시에 오던 버그의 원인).
    """
    month_total = await get_monthly_total()
    monthly_limit = get_monthly_limit()
    ratio = month_total / monthly_limit if monthly_limit else 0
    month_key = datetime.now().strftime("%Y-%m")

    for threshold in get_thresholds():
        if ratio >= threshold and not is_alerted(month_key, threshold):
            mark_alerted(month_key, threshold)
            await _send_threshold_alert(bot, threshold, month_total, monthly_limit)


async def _send_threshold_alert(bot, threshold: float, month_total: float, monthly_limit: float) -> None:
    import os
    cho_id = int(os.getenv("CHO_USER_ID", "0"))
    if not cho_id:
        return

    emoji = "🔴" if threshold >= 1.0 else "🟠" if threshold >= 0.7 else "🟡"
    color = 0xDC2626 if threshold >= 1.0 else 0xF97316 if threshold >= 0.7 else 0xEAB308
    title = f"{emoji} 인쵸 — 이번 달 크레딧 {int(threshold*100)}% 도달"

    try:
        user = await bot.fetch_user(cho_id)
        embed = discord.Embed(
            title=title,
            description=(
                f"이번 달 크레딧 사용률이 **{int(threshold*100)}%**를 넘었습니다.\n\n"
                f"사용: `${month_total:.3f}` / 한도 `${monthly_limit:.2f}`\n"
                f"잔여: `${monthly_limit - month_total:.3f}`"
            ),
            color=color,
        )
        if threshold >= 1.0:
            embed.add_field(
                name="⚠️ 조치 필요",
                value="월 한도 상향 또는 서비스 일시 중단 권고 (`/credit_limit`로 조정 가능)",
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
    from modules.fixed_costs import get_total_monthly_krw
    fixed_sum = await get_total_monthly_krw()
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

    # 임계치 알림 기록은 "YYYY-MM" 단위로 영속화되므로 달이 바뀌면 자동으로 리셋됨
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

# ── 자연어 자금 질의 처리 ─────────────────────────────────────────
async def handle_query(query: str) -> discord.Embed:
    """
    자연어 자금 질의 처리 — OpenRouter light 티어 (gpt-5.4-nano).
    /ask 명령이나 해쵸 오케스트레이션에서 호출됨.
    예: "이번 달 얼마 썼어?", "해쵸가 제일 돈 많이 쓰는 agent야?"
    """
    from utils.openrouter_client import chat
    from utils.notion_client import list_streamers

    try:
        # 컨텍스트 수집
        credits = await get_remaining_credits()
        month_total = await get_monthly_total()
        by_agent = await get_by_agent()
        by_model = await get_by_model()
        streamers = await list_streamers()
        n = len(streamers)

        # LLM에 전달할 컨텍스트
        agent_breakdown = (
            "\n".join(f"  - {a}: ${c:.4f}"
                      for a, c in sorted(by_agent.items(), key=lambda x: -x[1]))
            if by_agent else "  - (데이터 없음)"
        )
        model_breakdown = (
            "\n".join(f"  - {m.split('/')[-1]}: ${c:.4f}"
                      for m, c in sorted(by_model.items(), key=lambda x: -x[1])[:5])
            if by_model else "  - (데이터 없음)"
        )

        from modules.fixed_costs import get_costs
        fixed_costs = await get_costs()
        fixed_breakdown = (
            "\n".join(f"• {c['name']}: ₩{c['amount_krw']:,}" for c in fixed_costs)
            if fixed_costs else "• (등록된 고정비 없음)"
        )
        fixed_sum = sum(c["amount_krw"] for c in fixed_costs)

        context = f"""[현재 재무 스냅샷]
• 등록 스트리머: {n}명
• OpenRouter 크레딧: 사용 ${credits['usage']:.4f} / 총 ${credits['total']:.4f} ({credits['usage_ratio']*100:.1f}%)
• 잔여 크레딧: ${credits['remaining']:.4f}
• 이번 달 AI 토큰 누적: ${month_total:.4f} (≈ ₩{int(month_total * 1380):,})

[에이전트별 누적 비용]
{agent_breakdown}

[모델별 누적 비용 (상위 5개)]
{model_breakdown}

[월 고정비]
{fixed_breakdown}
• 합계: ₩{fixed_sum:,}
"""

        result = await chat(
            messages=[
                {"role": "system", "content":
                    "당신은 '인쵸'입니다. Cho의 매니지먼트 봇 자금 분석 전문가로서, "
                    "아래 실제 데이터만 근거로 답변하세요. 추측 금지. "
                    "숫자는 반드시 원문 그대로 인용하고, 필요 시 한화(원) 환산을 곁들이세요.\n\n"
                    + context},
                {"role": "user", "content": query},
            ],
            agent="inchyo",
            max_tokens=600,
            temperature=0.3,
        )

        embed = discord.Embed(
            title="💰 인쵸 — 자금 분석",
            description=result["content"][:3500],
            color=0x059669,
        )
        embed.set_footer(
            text=f"{result['model'].split('/')[-1]} · "
                 f"${result['cost']:.5f} · 인쵸"
        )
        return embed

    except Exception as e:
        log.error(f"인쵸 자연어 질의 오류: {e}")
        # 오류 시 기본 요약으로 폴백
        return await get_financial_summary()