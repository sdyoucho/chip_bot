"""
modules/haecho.py
해쵸 — Router 결과 기반 동적 오케스트레이터.

모델 사용:
- light (gpt-5.4-nano): 각 agent raw 결과를 사전 요약·정규화 (빠르게)
- premium (opus 4.7): 정규화된 결과를 최종 종합 브리핑
"""

import asyncio
import logging

import discord

from utils.openrouter_client import chat
from utils.pipeline_logger import step

log = logging.getLogger(__name__)

SUMMARY_SYSTEM = """당신은 Cho의 매니지먼트 총괄 AI '해쵸'입니다.
아래 역할 AI들의 결과를 종합해 Cho에게 브리핑합니다.
- 간결하되 실행 가능한 정보만
- 우선순위 순으로 정렬
- 각 항목 끝에 담당 에이전트 명시 [기쵸], [분쵸] 식
- 중복 정보는 한 번만 언급"""

PRECONDENSE_SYSTEM = """당신은 사전 정리 도우미입니다.
전달된 에이전트 원문을 3~5줄 bullet point로 요약하세요.
핵심 수치와 고유명사는 반드시 보존하고, 수사적 표현은 제거합니다."""


# ── Agent 핸들러 레지스트리 ─────────────────────────────────────────
async def _call_agent(name: str, query: str, streamer: str = "") -> tuple[discord.Embed, str]:
    """각 agent 호출 → (Embed, raw 텍스트). 실패해도 반드시 유효한 tuple 반환."""
    from modules import (
        chzzk_monitor, youtube_analytics, weekly_report,
        competitor_analysis, content_suggest, schedule,
        money, planning, rnd, design,
    )
    handlers = {
        "monitor":    lambda: chzzk_monitor.get_current_status(streamer or "all"),
        "youtube":    lambda: youtube_analytics.get_channel_stats(streamer or "all"),
        "report":     lambda: weekly_report.generate_report(streamer or "all"),
        "competitor": lambda: competitor_analysis.run_analysis(streamer or "all"),
        "suggest":    lambda: content_suggest.generate_suggestions(query, streamer),
        "schedule":   lambda: schedule.handle_schedule(query),
        "money":      lambda: money.handle_query(query) if query else money.get_financial_summary(),
        "planning":   lambda: planning.create_document(query, streamer),
        "rnd":        lambda: rnd.handle_query(query),
        "design":     lambda: design.handle_query(query),
    }
    handler = handlers.get(name)
    if not handler:
        err_embed = discord.Embed(
            title=f"❓ 알 수 없는 에이전트",
            description=f"`{name}` 에이전트를 찾을 수 없습니다.",
            color=0xEAB308,
        )
        return err_embed, f"unknown agent: {name}"

    try:
        embed = await handler()
        # 🛡️ 반환값 유효성 검증
        if embed is None or not isinstance(embed, discord.Embed):
            log.warning(f"agent {name}가 유효하지 않은 값 반환: {type(embed)}")
            err_embed = discord.Embed(
                title=f"⚠️ {name} 응답 형식 오류",
                description=f"에이전트가 예상치 못한 형식을 반환했습니다.",
                color=0xEAB308,
            )
            return err_embed, str(embed)

        # raw 텍스트 추출
        raw_parts = []
        if embed.title:
            raw_parts.append(embed.title)
        if embed.description:
            raw_parts.append(embed.description)
        for f in embed.fields:
            raw_parts.append(f"[{f.name}]\n{f.value}")
        raw = "\n\n".join(raw_parts)

        return embed, raw

    except Exception as e:
        log.exception(f"agent {name} 실행 실패")
        err = discord.Embed(
            title=f"❌ {name} 실행 중 오류",
            description=(
                f"**오류 내용**: {str(e)[:500]}\n\n"
                f"질문을 조금 다르게 표현하거나, `/ask 개쵸 {name} 문제 확인`으로 "
                f"개쵸에게 이슈를 공유해주세요."
            ),
            color=0xE11D48,
        )
        return err, f"error: {e}"


async def _precondense(agent_name: str, raw: str) -> str:
    """
    light 모델(gpt-5.4-nano)로 raw 결과를 빠르게 요약.
    premium 모델의 입력 토큰을 줄이는 선처리 단계.
    """
    if len(raw) < 400:
        return raw  # 이미 짧으면 생략

    try:
        result = await chat(
            messages=[
                {"role": "system", "content": PRECONDENSE_SYSTEM},
                {"role": "user", "content": f"[{agent_name}]\n{raw[:2000]}"},
            ],
            agent="haecho",
            tier="light",          # ⭐ 명시적으로 light 사용
            max_tokens=250,
            temperature=0.3,
            use_cache=True,
        )
        return result["content"]
    except Exception as e:
        log.warning(f"해쵸 사전 요약 실패({agent_name}): {e} → 원문 사용")
        return raw[:800]


async def orchestrate(
    query: str,
    routing: dict,
    streamer: str = "",
) -> dict:
    """
    Router 결과를 받아 필요한 agent만 병렬 호출.
    반환: {"agent_results": {name: (Embed, raw_text)}, "summary_embed": Embed | None}
    """
    modules = routing.get("modules", [])
    needs_summary = routing.get("needs_haecho_summary", False)

    step("해쵸 오케스트레이션", "ok",
         f"agents={[m['name'] for m in modules]}, summary={needs_summary}")

    # 1) 필요한 agent만 병렬 호출 (동시 3개 제한)
    sem = asyncio.Semaphore(3)

    async def _bound(m):
        async with sem:
            return m["name"], await _call_agent(m["name"], query, streamer)

    pairs = await asyncio.gather(
        *[_bound(m) for m in modules if m["name"] != "haecho"],
        return_exceptions=False,
    )
    agent_results = dict(pairs)

    # 2) 종합 브리핑 (필요 시)
    summary_embed = None
    if needs_summary or len(modules) >= 2:
        summary_embed = await _summarize(query, agent_results)

    return {"agent_results": agent_results, "summary_embed": summary_embed}


async def _summarize(query: str, agent_results: dict) -> discord.Embed:
    """
    light로 사전 요약 → premium으로 최종 종합.
    """
    # Step 1: 각 agent 결과를 light 모델로 병렬 사전 요약
    step("해쵸 사전 요약(light)", "ok", f"{len(agent_results)}개 agent")
    condense_tasks = {
        name: _precondense(name, raw)
        for name, (_, raw) in agent_results.items()
    }
    condensed_list = await asyncio.gather(*condense_tasks.values())
    condensed = dict(zip(condense_tasks.keys(), condensed_list))

    # Step 2: premium 모델로 최종 종합
    context_parts = [f"[{name}]\n{text}" for name, text in condensed.items()]
    context = "\n\n".join(context_parts) if context_parts else "(결과 없음)"
    user_msg = f"Cho 요청: {query}\n\n아래는 각 역할 AI의 정리된 결과입니다:\n\n{context}"

    try:
        result = await chat(
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            agent="haecho",
            tier="premium",        # ⭐ 명시적으로 premium(opus 4.7) 사용
            max_tokens=900,
            temperature=0.7,
            use_cache=False,
        )
        embed = discord.Embed(
            title="🎯 해쵸 — 총괄 브리핑",
            description=result["content"],
            color=0x1E293B,
        )
        embed.set_footer(
            text=f"{result['model'].split('/')[-1]} · "
                 f"${result['cost']:.5f} · "
                 f"light 사전정리 + premium 종합 · "
                 f"{len(agent_results)}개 에이전트"
        )
        return embed
    except Exception as e:
        log.exception("해쵸 종합 실패")
        from bot.embeds import embed_error
        return embed_error("해쵸 종합 실패", str(e))


# ── 기존 /ask 호환용 레거시 엔트리 ──────────────────────────────────
async def brief(query: str = "", streamer_name: str = "") -> discord.Embed:
    """단독 호출용 (라우터 없이 해쵸만 쓰는 경우)."""
    routing = {
        "modules": [
            {"name": "monitor",  "priority": 1, "reason": "현황"},
            {"name": "schedule", "priority": 2, "reason": "일정"},
            {"name": "money",    "priority": 3, "reason": "자금"},
        ],
        "needs_haecho_summary": True,
    }
    result = await orchestrate(query, routing, streamer_name)
    return result["summary_embed"] or discord.Embed(
        title="🎯 해쵸", description="결과 없음", color=0x1E293B
    )
